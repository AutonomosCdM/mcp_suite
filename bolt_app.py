import os
import asyncio
import time
from slack_bolt.async_app import AsyncApp
# Use the FastAPI adapter for Socket Mode to run both
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from dotenv import load_dotenv
from fastapi import FastAPI, Request # Import FastAPI and Request for integration

# Import shared utilities and agent logic
from supabase_utils import fetch_conversation_history, store_message
# Import the FastAPI app instance AND the agent initialization function
from mcp_agent_army_endpoint import app as fastapi_app, lifespan # Import FastAPI app and lifespan
from mcp_agent_army import get_mcp_agent_army # Still need this for lifespan
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart, TextPart

# Load environment variables
load_dotenv()

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN") # Socket Mode requires an App Token
SLACK_BOT_USER_ID = os.environ.get("SLACK_BOT_USER_ID")

if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
    print("Error: SLACK_BOT_TOKEN or SLACK_APP_TOKEN environment variables not set.")
    exit(1)

# Initialize Bolt App BEFORE FastAPI lifespan modifies its state
bolt_app = AsyncApp(token=SLACK_BOT_TOKEN)

# --- Event Handler for Messages ---
# Use bolt_app decorator
@bolt_app.message("") # Listen to all messages (DMs, channels, mentions if subscribed)
async def handle_message(message, say, context): # Use context to potentially access shared state later if needed
    """Handles incoming user messages."""
    # Acknowledge Slack immediately to prevent timeouts/retries (implicit in Bolt?)
    # await ack() # Bolt might handle basic ack automatically for messages

    event_user = message.get("user")
    text = message.get("text", "").strip()
    channel = message.get("channel")
    ts = message.get("ts") # Timestamp for potential threading later
    request_id = f"slack_socket_{ts}"
    session_id = f"slack_session_{channel}_{event_user}"

    print(f"Bolt received message from user {event_user} in channel {channel}: '{text}'")

    # Ignore messages from the bot itself or without a user
    if not event_user or event_user == SLACK_BOT_USER_ID:
        print("Ignoring message from bot or without user.")
        return

    # --- Quick Greeting Logic ---
    normalized_query = text.lower()
    greetings = ["hello", "hi", "hey", "hola", "yo", "sup"]
    if normalized_query in greetings:
        print("Greeting detected (Bolt), sending quick response.")
        quick_response = f"Hello there <@{event_user}>!"
        try:
            # Store messages
            await store_message(session_id=session_id, message_type="human", content=text)
            await store_message(session_id=session_id, message_type="ai", content=quick_response, data={"request_id": request_id, "quick_response": True})
            # Respond using Bolt's say function
            await say(text=quick_response)
            print("Quick response sent via Bolt.")
        except Exception as e:
            print(f"Error during Bolt quick response handling: {e}")
        return
    # --- End Quick Response Logic ---

    # --- Full Agent Processing ---
    # Access the agent stored in FastAPI app state via context if possible,
    # or fall back to a globally initialized one (less ideal but simpler for now)
    # This part needs careful handling of shared state between FastAPI lifespan and Bolt handlers.
    # For simplicity now, we might re-use the global approach temporarily,
    # acknowledging it's not ideal. Let's assume lifespan populates fastapi_app.state.primary_agent
    # How Bolt gets access needs clarification - maybe pass fastapi_app instance?

    # TEMPORARY: Relying on lifespan to set agent in fastapi_app.state
    # This handler needs access to that state. Bolt's default context might not have it.
    # Let's try accessing it directly via the imported fastapi_app instance for now.
    if not hasattr(fastapi_app.state, 'primary_agent') or fastapi_app.state.primary_agent is None:
        print("Error: Primary agent not found in FastAPI app state.")
        await say(text="Sorry, my brain isn't working right now (agent state issue). Please try again later.")
        return

    try:
        # Fetch history
        print(f"Fetching history for session_id: {session_id} (Bolt)")
        conversation_history = await fetch_conversation_history(session_id)
        print(f"Fetched {len(conversation_history)} messages (Bolt).")

        # Convert history
        messages = []
        for msg in conversation_history:
            msg_data = msg["message"]
            msg_type = msg_data["type"]
            msg_content = msg_data["content"]
            if isinstance(msg_content, str):
                 msg_obj = ModelRequest(parts=[UserPromptPart(content=msg_content)]) if msg_type == "human" else ModelResponse(parts=[TextPart(content=msg_content)])
                 messages.append(msg_obj)
            else:
                 print(f"Warning: Skipping message with non-string content: {msg_content}")

        # Store incoming message
        print(f"Storing user message for session_id: {session_id} (Bolt)")
        await store_message(session_id=session_id, message_type="human", content=text)

        # Run the agent
        print(f"Running primary agent for query: '{text}' (Bolt)")
        agent_instance = fastapi_app.state.primary_agent # Get from state
        result = await agent_instance.run(text, message_history=messages)
        response_text = result.data if hasattr(result, "data") else str(result)
        print(f"Agent returned response: '{response_text}' (Bolt)")

        # Store agent response
        await store_message(session_id=session_id, message_type="ai", content=response_text, data={"request_id": request_id})

        # Send response back using Bolt's say function
        await say(text=response_text)
        print("Agent response sent via Bolt.")

    except Exception as e:
        print(f"General error during Bolt processing: {e}")
        try:
            await say(text="Sorry, I encountered an error processing your request.")
        except Exception as say_err:
            print(f"Failed to send error message via Bolt: {say_err}")


# --- Main execution ---
# We won't run Bolt standalone. FastAPI will run via Uvicorn,
# and the lifespan manager will initialize the agent.
# We need to ensure the Socket Mode handler runs alongside FastAPI.

# One way is to start the Socket Mode handler in a background task
# within the FastAPI lifespan startup.

# Modify FastAPI lifespan in mcp_agent_army_endpoint.py instead.
# This bolt_app.py file now only defines the Bolt app and its handlers.

# Remove the standalone main execution block
# async def main(): ...
# if __name__ == "__main__": ...
