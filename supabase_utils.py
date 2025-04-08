import os
from typing import List, Optional, Dict, Any
from supabase import create_client, Client
from fastapi import HTTPException # Keep HTTPException for raising errors

# Load environment variables (needed for Supabase creds)
# Consider a shared config module later if needed
from dotenv import load_dotenv
load_dotenv()

# Supabase setup
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_SERVICE_KEY")

if not supabase_url or not supabase_key:
    print("Warning: SUPABASE_URL or SUPABASE_SERVICE_KEY environment variables not set.")
    # Allow initialization but functions will likely fail
    supabase: Client | None = None
else:
    supabase: Client = create_client(supabase_url, supabase_key)

async def fetch_conversation_history(session_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Fetch the most recent conversation history for a session."""
    if not supabase:
        print("Error: Supabase client not initialized.")
        # Or raise an internal server error
        raise HTTPException(status_code=500, detail="Supabase client not initialized")
    try:
        response = supabase.table("messages") \
            .select("*") \
            .eq("session_id", session_id) \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()

        # Convert to list and reverse to get chronological order
        messages = response.data[::-1] if response.data else []
        return messages
    except Exception as e:
        print(f"Error fetching Supabase history: {e}")
        # Raise HTTPException so FastAPI handles it
        raise HTTPException(status_code=500, detail=f"Failed to fetch conversation history: {str(e)}")

async def store_message(session_id: str, message_type: str, content: str, data: Optional[Dict] = None):
    """Store a message in the Supabase messages table."""
    if not supabase:
        print("Error: Supabase client not initialized.")
        # Or raise an internal server error
        raise HTTPException(status_code=500, detail="Supabase client not initialized")

    message_obj = {
        "type": message_type,
        "content": content
    }
    if data:
        message_obj["data"] = data

    try:
        insert_data = {
            "session_id": session_id,
            "message": message_obj
        }
        print(f"Attempting to store message: {insert_data}") # Debug print
        response = supabase.table("messages").insert(insert_data).execute()
        print(f"Supabase store response: {response}") # Debug print
        if hasattr(response, 'error') and response.error:
             print(f"Supabase error details: {response.error}")
             raise HTTPException(status_code=500, detail=f"Supabase error: {response.error.message}")

    except Exception as e:
        print(f"Error storing message to Supabase: {e}")
        # Raise HTTPException so FastAPI handles it
        raise HTTPException(status_code=500, detail=f"Failed to store message: {str(e)}")
