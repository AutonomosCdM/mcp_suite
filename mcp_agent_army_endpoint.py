from typing import List, Optional, Dict, Any
from fastapi import FastAPI, Request, HTTPException, Security, Depends # Import Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
# Supabase client is now initialized in supabase_utils
# from supabase import create_client, Client
from pydantic import BaseModel
from dotenv import load_dotenv
from pathlib import Path
import httpx
import sys
import os
import asyncio # Import asyncio for background task

# Import Bolt and SocketModeHandler
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from bolt_app import app as bolt_app # Import the bolt app instance

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    UserPromptPart,
    TextPart
)

from mcp_agent_army import get_mcp_agent_army

# Load environment variables
load_dotenv()

# --- Lifespan Manager ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize resources
    print("Lifespan: Initializing MCP Agent Army...")
    agent, mcp_stack = await get_mcp_agent_army()
    app.state.primary_agent = agent # Store agent in app state
    app.state.mcp_stack = mcp_stack # Store stack in app state (needed for cleanup)
    print("Lifespan: MCP Agent Army Initialized.")

    # Start Socket Mode Handler in background
    SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN")
    if SLACK_APP_TOKEN:
        print("Lifespan: Starting Bolt Socket Mode Handler...")
        socket_handler = AsyncSocketModeHandler(bolt_app, SLACK_APP_TOKEN)
        # Run the handler in a background task
        socket_task = asyncio.create_task(socket_handler.start_async())
        app.state.socket_task = socket_task # Store task to potentially cancel later
        print("Lifespan: Bolt Socket Mode Handler started.")
    else:
        print("Warning: SLACK_APP_TOKEN not set. Bolt Socket Mode Handler not started.")
        app.state.socket_task = None

    yield # Application runs here

    # Cleanup: close the MCP servers and potentially stop socket task
    print("Lifespan: Shutting down MCP servers...")
    await app.state.mcp_stack.aclose()
    print("Lifespan: MCP servers shut down.")
    if app.state.socket_task and not app.state.socket_task.done():
        print("Lifespan: Cancelling Bolt Socket Mode Handler task...")
        app.state.socket_task.cancel()
        try:
            await app.state.socket_task
        except asyncio.CancelledError:
            print("Lifespan: Bolt Socket Mode Handler task cancelled.")

# --- FastAPI App Initialization ---
app = FastAPI(lifespan=lifespan)
security = HTTPBearer()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Shared Utilities Import ---
# Import shared Supabase functions
from supabase_utils import fetch_conversation_history, store_message

# --- Request/Response Models ---
class AgentRequest(BaseModel):
    query: str
    user_id: str
    request_id: str
    session_id: str

class AgentResponse(BaseModel):
    success: bool

# --- Authentication ---
def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> bool:
    """Verify the bearer token against environment variable."""
    expected_token = os.getenv("API_BEARER_TOKEN")
    if not expected_token:
        raise HTTPException(
            status_code=500,
            detail="API_BEARER_TOKEN environment variable not set"
        )
    if credentials.credentials != expected_token:
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token"
        )
    return True

# --- API Endpoints ---
@app.get("/")
async def read_root():
    print("🔍 Received request for root /")
    return {"status": "ok", "message": "MCP Agent Army Endpoint is running!"}

# Note: Slack router is removed as Bolt handles Slack events now

@app.post("/api/mcp-agent-army", response_model=AgentResponse)
async def mcp_agent_army(
    agent_request: AgentRequest, # Incoming data model
    request: Request,           # FastAPI Request object to access app state
    authenticated: bool = Depends(verify_token)
):
    # Use agent_request for data, request for app state
    print(f"🔍 Received API request for session_id: {agent_request.session_id}, request_id: {agent_request.request_id}, query: '{agent_request.query}'")

    # --- Quick Response Logic (for API endpoint) ---
    normalized_query = agent_request.query.strip().lower()
    greetings = ["hello", "hi", "hey", "hola", "yo", "sup"]
    if normalized_query in greetings:
        print("API Greeting detected, sending quick response.")
        quick_response = f"Hello there, {agent_request.user_id}!"
        # Store user query and quick response
        await store_message(session_id=agent_request.session_id, message_type="human", content=agent_request.query)
        await store_message(session_id=agent_request.session_id, message_type="ai", content=quick_response, data={"request_id": agent_request.request_id, "quick_response": True})
        return AgentResponse(success=True)
    # --- End Quick Response Logic ---

    try:
        # Fetch conversation history
        print(f"API Fetching history for session_id: {agent_request.session_id}")
        conversation_history = await fetch_conversation_history(agent_request.session_id)
        print(f"API Fetched {len(conversation_history)} messages from history.")

        # Convert conversation history to format expected by agent
        messages = []
        for msg in conversation_history:
            msg_data = msg["message"]
            msg_type = msg_data["type"]
            msg_content = msg_data["content"]
            # Ensure content is string
            if isinstance(msg_content, str):
                 msg_obj = ModelRequest(parts=[UserPromptPart(content=msg_content)]) if msg_type == "human" else ModelResponse(parts=[TextPart(content=msg_content)])
                 messages.append(msg_obj)
            else:
                 print(f"Warning: Skipping message with non-string content: {msg_content}")


        # Store user's query
        print(f"API Storing user query for session_id: {agent_request.session_id}")
        await store_message(
            session_id=agent_request.session_id,
            message_type="human",
            content=agent_request.query
        )
        print(f"API User query stored. Running primary agent...")

        # Get agent from app state using the FastAPI Request object
        agent = request.app.state.primary_agent
        if not agent:
             print("Error: Primary agent not found in app state.")
             raise HTTPException(status_code=500, detail="Agent not initialized")

        # Run the agent with conversation history
        result = await agent.run(
            agent_request.query,
            message_history=messages
        )
        response_text = result.data if hasattr(result, "data") else str(result)
        print(f"API Agent returned response: '{response_text}'")


        # Store agent's response
        await store_message(
            session_id=agent_request.session_id,
            message_type="ai",
            content=response_text, # Use response_text
            data={"request_id": agent_request.request_id}
        )
        print(f"API Agent response stored. Request successful.")

        # Update memories based on the last user message and agent response
        # memory_messages = [
        #     {"role": "user", "content": agent_request.query},
        #     {"role": "assistant", "content": response_text} # Use response_text
        # ]

        return AgentResponse(success=True)

    except Exception as e:
        print(f"Error processing API request: {str(e)}")
        # Store error message in conversation
        try:
            await store_message(
                session_id=agent_request.session_id,
                message_type="ai",
                content="I apologize, but I encountered an error processing your request.",
                data={"error": str(e), "request_id": agent_request.request_id}
            )
            print(f"API Error handled and stored. Returning success=False.")
        except Exception as store_err:
             print(f"Error storing error message to Supabase: {store_err}")
        return AgentResponse(success=False)

# Note: The if __name__ == "__main__": block is for local testing.
# Railway will use the CMD in the Dockerfile.
if __name__ == "__main__":
    import uvicorn
    # This won't run the Socket Mode handler when run directly like this.
    # For local testing of both, you'd need a different entry point
    # or run bolt_app.py separately.
    print("Running FastAPI app directly (Socket Mode handler will not start automatically)")
    uvicorn.run(app, host="0.0.0.0", port=8001)
