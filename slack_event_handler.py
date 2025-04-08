import os
import time
import hmac
import hashlib
import json
from fastapi import APIRouter, Request, HTTPException, Depends, BackgroundTasks # Import BackgroundTasks
from fastapi.responses import JSONResponse
from slack_sdk.web.async_client import AsyncWebClient # Use async client
from slack_sdk.errors import SlackApiError
from pydantic_ai import Agent # Import Agent for type hinting

# Import shared utilities
from supabase_utils import fetch_conversation_history, store_message
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart, TextPart

router = APIRouter()

# Load secrets from environment
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

if not SLACK_SIGNING_SECRET or not SLACK_BOT_TOKEN:
    print("Warning: SLACK_SIGNING_SECRET or SLACK_BOT_TOKEN environment variables not set.")
    # Allow initialization but endpoint will likely fail signature check or Slack API calls
    slack_client = None
else:
    slack_client = AsyncWebClient(token=SLACK_BOT_TOKEN)

# --- HMAC Signature Verification ---
async def verify_slack_signature(request: Request) -> bool:
    """Verifies the request signature using the Slack signing secret."""
    if not SLACK_SIGNING_SECRET:
        print("Error: SLACK_SIGNING_SECRET not configured.")
        return False # Cannot verify without the secret

    timestamp = request.headers.get("X-Slack-Request-Timestamp")
    slack_signature = request.headers.get("X-Slack-Signature")
    body = await request.body() # Read body only once

    if not timestamp or not slack_signature:
        print("Warning: Missing Slack signature headers.")
        return False

    try:
        if abs(time.time() - int(timestamp)) > 60 * 5:
            print("Warning: Slack request timestamp too old.")
            return False  # Request timestamp is too old
    except ValueError:
        print("Warning: Invalid Slack timestamp header.")
        return False # Invalid timestamp format

    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    my_signature = 'v0=' + hmac.new(
        SLACK_SIGNING_SECRET.encode('utf-8'), # Ensure bytes
        sig_basestring.encode('utf-8'),      # Ensure bytes
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(my_signature, slack_signature)

# --- Background Task for Processing ---
async def process_slack_event(
    text: str,
    user_id: str,
    channel: str,
    session_id: str,
    request_id: str,
    primary_agent: Agent, # Pass the agent instance
    slack_client: AsyncWebClient | None # Pass the slack client instance
):
    """Handles the actual processing of the Slack message in the background."""
    print(f"Background task started for request_id: {request_id}")

    # --- Quick Greeting Logic ---
    normalized_query = text.lower()
    greetings = ["hello", "hi", "hey", "hola", "yo", "sup"]
    if normalized_query in greetings:
        print("Greeting detected (background), sending quick response to Slack.")
        quick_response = f"Hello there <@{user_id}>!"
        try:
            # Store user query and quick response
            await store_message(session_id=session_id, message_type="human", content=text)
            await store_message(session_id=session_id, message_type="ai", content=quick_response, data={"request_id": request_id, "quick_response": True})
            if slack_client:
                await slack_client.chat_postMessage(channel=channel, text=quick_response)
                print("Quick response sent to Slack.")
            else:
                 print("Error: Slack client not initialized, cannot send quick response.")
        except Exception as e:
            print(f"Error during background quick response handling: {e}")
        return # End processing for greetings
    # --- End Quick Response Logic ---

    # --- Full Agent Processing ---
    try:
        # Fetch history
        print(f"Fetching history for session_id: {session_id} (background)")
        conversation_history = await fetch_conversation_history(session_id)
        print(f"Fetched {len(conversation_history)} messages (background).")

         # Convert conversation history to format expected by agent
        messages = []
        for msg in conversation_history:
            msg_data = msg["message"]
            msg_type = msg_data["type"]
            msg_content = msg_data["content"]
            # Ensure content is string, handle potential non-string data if necessary
            if isinstance(msg_content, str):
                 msg_obj = ModelRequest(parts=[UserPromptPart(content=msg_content)]) if msg_type == "human" else ModelResponse(parts=[TextPart(content=msg_content)])
                 messages.append(msg_obj)
            else:
                 print(f"Warning: Skipping message with non-string content: {msg_content}")

        # Store incoming user message (already stored before starting background task usually, but maybe store again here for atomicity?)
        # Let's assume storing before background task is sufficient for now. If issues arise, reconsider.
        # print(f"Storing user message for session_id: {session_id} (background)")
        # await store_message(session_id=session_id, message_type="human", content=text)

        # Run the agent
        print(f"Running primary agent for query: '{text}' (background)")
        result = await primary_agent.run(text, message_history=messages)
        response_text = result.data if hasattr(result, "data") else str(result)
        print(f"Agent returned response: '{response_text}' (background)")

        # Store agent response
        await store_message(session_id=session_id, message_type="ai", content=response_text, data={"request_id": request_id})

        # Send response back to Slack
        if slack_client:
            await slack_client.chat_postMessage(channel=channel, text=response_text)
            print("Agent response sent to Slack.")
        else:
            print("Error: Slack client not initialized, cannot send agent response.")

    except SlackApiError as e:
        print(f"Slack API Error during background processing: {e.response['error']}")
    except HTTPException as e:
         # If Supabase utils raise HTTPException, log it
         print(f"HTTPException during background processing: {e.detail}")
    except Exception as e:
        print(f"General error during background processing: {e}")
        # Try to send a generic error message back to Slack
        if slack_client:
            try:
                await slack_client.chat_postMessage(channel=channel, text="Sorry, I encountered an error processing your request.")
            except SlackApiError as slack_err:
                 print(f"Failed to send error message to Slack: {slack_err.response['error']}")

# --- Slack Events Endpoint ---
@router.post("/slack/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks): # Add BackgroundTasks
    # Verify signature first
    if not await verify_slack_signature(request):
         print("Signature verification failed!") # Add log for failure
         raise HTTPException(status_code=401, detail="Invalid Slack signature")
    else:
         print("Signature verification successful!") # Add log for success

    # Need to parse body after verification uses the raw body
    # Read body once for verification and parsing
    body_bytes = await request.body()
    try:
        payload = json.loads(body_bytes)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Respond to Slack challenge event for URL verification
    if payload.get("type") == "url_verification":
        print("Received Slack URL verification challenge.")
        return JSONResponse(content={"challenge": payload.get("challenge")})

    # Handle actual events
    if payload.get("type") == "event_callback":
        event = payload.get("event", {})
        event_type = event.get("type")
        # Ignore messages from bots or message edits/deletions etc.
        if event_type == "message" and "subtype" not in event and event.get("user"):
            user_id = event.get("user")
            text = event.get("text", "").strip()
            channel = event.get("channel")
            request_id = payload.get("api_app_id", "unknown_app") + "_" + event.get("event_ts", str(time.time()))
            session_id = f"slack_session_{channel}_{user_id}" # Include channel for context

            print(f"Received message from user {user_id} in channel {channel}: '{text}'")

            # Get the shared agent instance from app state
            primary_agent_instance = request.app.state.primary_agent
            if not primary_agent_instance:
                 print("Error: Primary agent not found in app state. Cannot process event.")
                 # Acknowledge Slack, but log the error server-side
                 return JSONResponse(content={"ok": True})

            # Schedule the processing to run in the background
            background_tasks.add_task(
                process_slack_event,
                text=text,
                user_id=user_id,
                channel=channel,
                session_id=session_id,
                request_id=request_id,
                primary_agent=primary_agent_instance,
                slack_client=slack_client
            )
            print(f"Scheduled background task for request_id: {request_id}")

    # Acknowledge Slack immediately for all valid event_callbacks we don't explicitly ignore
    # or that have been scheduled for background processing.
    return JSONResponse(content={"ok": True})
