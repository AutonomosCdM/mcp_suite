from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from supabase import create_client, Client
from pydantic import BaseModel
from dotenv import load_dotenv
from pathlib import Path
import httpx
import sys
import os

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    UserPromptPart,
    TextPart
)

from mcp_agent_army import get_mcp_agent_army

# Load environment variables
load_dotenv()

# Remove global variables for agent/stack
# primary_agent = None
# mcp_stack = None

# Define a lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize resources
    print("Lifespan: Initializing MCP Agent Army...")
    agent, stack = await get_mcp_agent_army()
    app.state.primary_agent = agent # Store agent in app state
    app.state.mcp_stack = stack     # Store stack in app state (needed for cleanup)
    print("Lifespan: MCP Agent Army Initialized.")

    yield

    # Cleanup: close the MCP servers when the application shuts down
    print("Lifespan: Shutting down MCP servers...")
    await app.state.mcp_stack.aclose()
    print("Lifespan: MCP servers shut down.")

# Initialize FastAPI app
app = FastAPI(lifespan=lifespan)
security = HTTPBearer()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add a simple root endpoint for health check / basic testing
@app.get("/")
async def read_root():
    print("🔍 Received request for root /")
    return {"status": "ok", "message": "MCP Agent Army Endpoint is running!"}

# Remove Supabase setup here, it's moved to supabase_utils.py
# supabase: Client = create_client(...)

# Import shared Supabase functions
from supabase_utils import fetch_conversation_history, store_message

# Import the Slack event router
from slack_event_handler import router as slack_router

# Request/Response Models
class AgentRequest(BaseModel):
    query: str
    user_id: str
    request_id: str
    session_id: str

class AgentResponse(BaseModel):
    success: bool

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

# Remove fetch_conversation_history and store_message functions (moved to supabase_utils.py)

# Include the Slack router
app.include_router(slack_router)

@app.post("/api/mcp-agent-army", response_model=AgentResponse)
async def mcp_agent_army(
    request: AgentRequest,
    authenticated: bool = Depends(verify_token)
):
    print(f"🔍 Received request for session_id: {request.session_id}, request_id: {request.request_id}, query: '{request.query}'") # ADDED DEBUG LOG + query

    # --- Quick Response Logic ---
    normalized_query = request.query.strip().lower()
    greetings = ["hello", "hi", "hey", "hola", "yo", "sup"]
    if normalized_query in greetings:
        print("Greeting detected, sending quick response.")
        quick_response = f"Hello there, {request.user_id}!"
        # Store user query and quick response
        await store_message(session_id=request.session_id, message_type="human", content=request.query)
        await store_message(session_id=request.session_id, message_type="ai", content=quick_response, data={"request_id": request.request_id, "quick_response": True})
        return AgentResponse(success=True) # Note: We might want a different response structure later
    # --- End Quick Response Logic ---

    try:
        # Fetch conversation history
        print(f"Fetching history for session_id: {request.session_id}") # ADDED DEBUG LOG
        conversation_history = await fetch_conversation_history(request.session_id)
        print(f"Fetched {len(conversation_history)} messages from history.") # ADDED DEBUG LOG
        
        # Convert conversation history to format expected by agent
        messages = []
        for msg in conversation_history:
            msg_data = msg["message"]
            msg_type = msg_data["type"]
            msg_content = msg_data["content"]
            msg = ModelRequest(parts=[UserPromptPart(content=msg_content)]) if msg_type == "human" else ModelResponse(parts=[TextPart(content=msg_content)])
            messages.append(msg)

        # Store user's query
        print(f"Storing user query for session_id: {request.session_id}") # ADDED DEBUG LOG
        await store_message(
            session_id=request.session_id,
            message_type="human",
            content=request.query
        )
        print(f"User query stored. Running primary agent...") # ADDED DEBUG LOG

        # Get agent from app state
        agent = request.app.state.primary_agent

        # Run the agent with conversation history
        result = await agent.run(
            request.query,
            message_history=messages
        )

        # Store agent's response
        await store_message(
            session_id=request.session_id,
            message_type="ai",
            content=result.data,
            data={"request_id": request.request_id}
        )
        print(f"Agent response stored. Request successful.") # ADDED DEBUG LOG

        # Update memories based on the last user message and agent response
        memory_messages = [
            {"role": "user", "content": request.query},
            {"role": "assistant", "content": result.data}
        ]   

        return AgentResponse(success=True)

    except Exception as e:
        print(f"Error processing agent request: {str(e)}")
        # Store error message in conversation
        await store_message(
            session_id=request.session_id,
            message_type="ai",
            content="I apologize, but I encountered an error processing your request.",
            data={"error": str(e), "request_id": request.request_id}
        )
        print(f"Error handled and stored. Returning success=False.") # ADDED DEBUG LOG
        return AgentResponse(success=False)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
