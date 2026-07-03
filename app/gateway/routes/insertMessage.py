from uuid import UUID
from datetime import datetime
from typing import Optional, Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# Shared service-role client (see gateway/database.py) — avoids a second
# create_client() against the same project.
from app.gateway.database import supabase

router = APIRouter(prefix="/api/messages", tags=["Message Management"])

# Pydantic model for request validation
class MessageCreate(BaseModel):
    userid: UUID
    message: str
    role: Literal["student", "tutor"] = Field(
        ..., description="Who sent the message: 'student' or 'tutor'"
    )
    conversation_id: Optional[UUID] = None

@router.post("/insert")
async def insert_message(data: MessageCreate):
    try:
        payload = {
            "userid": str(data.userid),
            "message": data.message,
            "role": data.role,
            "timestamp": datetime.now().isoformat(),
            "processed": False
        }

        if data.conversation_id is not None:
            payload["conversation_id"] = str(data.conversation_id)

        response = supabase.table("messages").insert(payload).execute()

        return {
            "status": "success",
            "data": response.data
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))