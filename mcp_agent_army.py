from __future__ import annotations
from contextlib import AsyncExitStack
from typing import Any, Dict, List
from dataclasses import dataclass
from dotenv import load_dotenv
from rich.markdown import Markdown
from rich.console import Console
from rich.live import Live
import asyncio
import os

from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.models.gemini import GeminiModel # Re-add GeminiModel import
from pydantic_ai.mcp import MCPServerStdio
from pydantic_ai import Agent, RunContext

load_dotenv()

# ========== Helper function to get model configuration ==========
def get_model():
    provider_name = os.getenv('PROVIDER', 'Gemini').lower() # Default to Gemini
    llm = os.getenv('MODEL_CHOICE')

    if not llm:
        # Set default model based on provider if not specified
        if provider_name == 'gemini':
            llm = 'gemini-2.0-flash' # Default Gemini model
        else:
            llm = 'gpt-4o-mini' # Default OpenAI/Groq model
        print(f"Warning: MODEL_CHOICE not set, defaulting to {llm} for provider {provider_name}")

    if provider_name == 'gemini':
        # API key is typically handled by google-generativeai library via env GOOGLE_API_KEY
        # or application default credentials. We don't pass it directly here.
        print(f"Using Gemini provider with model: {llm}")
        # Initialize GeminiModel directly with provider string
        # Ensure GEMINI_API_KEY is set in the environment for the underlying library
        if not os.getenv('GEMINI_API_KEY'):
             print("Warning: GEMINI_API_KEY not found in environment variables. Ensure it's set for GeminiModel.")
        # Assuming 'google-gla' is the correct provider string for the standard Gemini API
        return GeminiModel(llm, provider='google-gla')

    elif provider_name == 'openai' or provider_name == 'groq':
        base_url = os.getenv('BASE_URL')
        if not base_url:
             if provider_name == 'openai':
                 base_url = 'https://api.openai.com/v1'
             elif provider_name == 'groq':
                 base_url = 'https://api.groq.com/openai/v1'
             print(f"Warning: BASE_URL not set, defaulting to {base_url} for provider {provider_name}")

        api_key = None
        if provider_name == 'openai':
            api_key = os.getenv('LLM_API_KEY')
        elif provider_name == 'groq':
            api_key = os.getenv('GROQ_API_KEY')

        if not api_key:
            api_key = os.getenv('LLM_API_KEY') # Fallback

        if not api_key:
            print(f"Warning: API key not found for provider '{provider_name}'. Check LLM_API_KEY or {provider_name.upper()}_API_KEY.")
            api_key = 'no-api-key-provided'

        print(f"Using OpenAI compatible provider ({provider_name}) with model: {llm} at base_url: {base_url}")
        return OpenAIModel(llm, provider=OpenAIProvider(base_url=base_url, api_key=api_key))

    else:
        raise ValueError(f"Unsupported PROVIDER: {provider_name}. Supported providers are OpenAI, Gemini, Groq.")

# ========== Set up MCP servers for each service ==========

# Airtable MCP server
airtable_server = MCPServerStdio(
    'npx', ['airtable-mcp-server'], # Removed -y, package name is arg
    env={"AIRTABLE_API_KEY": os.getenv("AIRTABLE_API_KEY")}
)

# Brave Search MCP server
brave_server = MCPServerStdio(
    'npx', ['@modelcontextprotocol/server-brave-search'], # Removed -y
    env={"BRAVE_API_KEY": os.getenv("BRAVE_API_KEY")}
)

# Filesystem MCP server
# Note: LOCAL_FILE_DIR needs to be valid within the container
filesystem_server = MCPServerStdio(
    'npx', ['@modelcontextprotocol/server-filesystem', os.getenv("LOCAL_FILE_DIR", "/app/local_files")], # Use /app/local_files as default
    env={} # No specific env needed for filesystem server itself
)

# GitHub MCP server
github_server = MCPServerStdio(
    'npx', ['@modelcontextprotocol/server-github'], # Removed -y
    env={"GITHUB_PERSONAL_ACCESS_TOKEN": os.getenv("GITHUB_TOKEN")}
)

# Slack MCP server
slack_server = MCPServerStdio(
    'npx', ['@modelcontextprotocol/server-slack'], # Removed -y
    env={
        "SLACK_BOT_TOKEN": os.getenv("SLACK_BOT_TOKEN"),
        "SLACK_TEAM_ID": os.getenv("SLACK_TEAM_ID")
    }
)

# Firecrawl MCP server
firecrawl_server = MCPServerStdio(
    'npx', ['firecrawl-mcp'], # Removed -y
    env={"FIRECRAWL_API_KEY": os.getenv("FIRECRAWL_API_KEY")}
)

# ========== Create subagents with their MCP servers ==========

# Airtable agent
airtable_agent = Agent(
    get_model(),
    system_prompt="You are an Airtable specialist. Help users interact with Airtable databases.",
    mcp_servers=[airtable_server]
)

# Brave search agent
brave_agent = Agent(
    get_model(),
    system_prompt="You are a web search specialist using Brave Search. Find relevant information on the web.",
    mcp_servers=[brave_server]
)

# Filesystem agent
filesystem_agent = Agent(
    get_model(),
    system_prompt="You are a filesystem specialist. Help users manage their files and directories.",
    mcp_servers=[filesystem_server]
)

# GitHub agent
github_agent = Agent(
    get_model(),
    system_prompt="You are a GitHub specialist. Help users interact with GitHub repositories and features.",
    mcp_servers=[github_server]
)

# Slack agent
slack_agent = Agent(
    get_model(),
    system_prompt="You are a Slack specialist. Help users interact with Slack workspaces and channels.",
    mcp_servers=[slack_server]
)

# Firecrawl agent
firecrawl_agent = Agent(
    get_model(),
    system_prompt="You are a web crawling specialist. Help users extract data from websites.",
    mcp_servers=[firecrawl_server]
)

# ========== Create the primary orchestration agent ==========
primary_agent = Agent(
    get_model(),
    system_prompt="""You are a primary orchestration agent that can call upon specialized subagents
    to perform various tasks. Each subagent is an expert in interacting with a specific third-party service.
    Analyze the user request and delegate the work to the appropriate subagent.

    IMPORTANT: When processing a request originating from a Slack event handler, your final output should be ONLY the text content of the response intended for the user. The handler itself will send this text back to the appropriate Slack channel. Do NOT use the slack_agent tool to send the final response in this context. Only use the slack_agent if the user's request is specifically asking you to perform a distinct action within Slack (e.g., 'send a message to #general', 'find user X')."""
)

# ========== Define tools for the primary agent to call subagents ==========

@primary_agent.tool_plain
async def use_airtable_agent(query: str) -> dict[str, str]:
    """
    Access and manipulate Airtable data through the Airtable subagent.
    Use this tool when the user needs to fetch, modify, or analyze data in Airtable.

    Args:
        ctx: The run context.
        query: The instruction for the Airtable agent.

    Returns:
        The response from the Airtable agent.
    """
    print(f"Calling Airtable agent with query: {query}")
    result = await airtable_agent.run(query)
    return {"result": result.data}

@primary_agent.tool_plain
async def use_brave_search_agent(query: str) -> dict[str, str]:
    """
    Search the web using Brave Search through the Brave subagent.
    Use this tool when the user needs to find information on the internet or research a topic.

    Args:
        ctx: The run context.
        query: The search query or instruction for the Brave search agent.

    Returns:
        The search results or response from the Brave agent.
    """
    print(f"Calling Brave agent with query: {query}")
    result = await brave_agent.run(query)
    return {"result": result.data}

@primary_agent.tool_plain
async def use_filesystem_agent(query: str) -> dict[str, str]:
    """
    Interact with the file system through the filesystem subagent.
    Use this tool when the user needs to read, write, list, or modify files.

    Args:
        ctx: The run context.
        query: The instruction for the filesystem agent.

    Returns:
        The response from the filesystem agent.
    """
    print(f"Calling Filesystem agent with query: {query}")
    result = await filesystem_agent.run(query)
    return {"result": result.data}

@primary_agent.tool_plain
async def use_github_agent(query: str) -> dict[str, str]:
    """
    Interact with GitHub through the GitHub subagent.
    Use this tool when the user needs to access repositories, issues, PRs, or other GitHub resources.

    Args:
        ctx: The run context.
        query: The instruction for the GitHub agent.

    Returns:
        The response from the GitHub agent.
    """
    print(f"Calling GitHub agent with query: {query}")
    result = await github_agent.run(query)
    return {"result": result.data}

@primary_agent.tool_plain
async def use_slack_agent(query: str) -> dict[str, str]:
    """
    Interact with Slack through the Slack subagent.
    Use this tool when the user needs to send messages, access channels, or retrieve Slack information.

    Args:
        ctx: The run context.
        query: The instruction for the Slack agent.

    Returns:
        The response from the Slack agent.
    """
    print(f"Calling Slack agent with query: {query}")
    result = await slack_agent.run(query)
    return {"result": result.data}

@primary_agent.tool_plain
async def use_firecrawl_agent(query: str) -> dict[str, str]:
    """
    Crawl and analyze websites using the Firecrawl subagent.
    Use this tool when the user needs to extract data from websites or perform web scraping.

    Args:
        ctx: The run context.
        query: The instruction for the Firecrawl agent.

    Returns:
        The response from the Firecrawl agent.
    """
    print(f"Calling Firecrawl agent with query: {query}")
    result = await firecrawl_agent.run(query)
    return {"result": result.data}

async def get_mcp_agent_army():
    """
    Initialize and return the primary agent with all MCP servers running.
    This function sets up an AsyncExitStack and starts all MCP servers,
    then returns the primary agent ready to use.
    
    Returns:
        tuple: (primary_agent, stack) - The primary agent and the AsyncExitStack
              that must be kept alive to maintain the MCP server connections
    """
    # Create a new AsyncExitStack that will be returned to the caller
    stack = AsyncExitStack()
    
    # Start all the subagent MCP servers
    print("Starting MCP servers...")
    await stack.enter_async_context(airtable_agent.run_mcp_servers())
    await stack.enter_async_context(brave_agent.run_mcp_servers())
    await stack.enter_async_context(filesystem_agent.run_mcp_servers())
    await stack.enter_async_context(github_agent.run_mcp_servers())
    await stack.enter_async_context(slack_agent.run_mcp_servers())
    await stack.enter_async_context(firecrawl_agent.run_mcp_servers())
    print("All MCP servers started successfully!")
    
    # Return both the primary agent and the stack
    return primary_agent, stack
