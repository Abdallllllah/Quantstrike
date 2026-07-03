
import os
import json
import redis
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv, find_dotenv
from app.gateway.routes.llm_clients import openrouter_client, GEMINI_MODEL

load_dotenv(find_dotenv())

router = APIRouter(prefix="/api/extract", tags=["Qwen Extractor"])

# 1. Redis Setup
redis_url = os.getenv("REDIS_URL")
if not redis_url:
    raise ValueError("REDIS_URL not found in environment variables!")

redis_client = redis.from_url(
    redis_url,
    decode_responses=True,
)

# Your Internal Retrieval URL
BASE_URL = os.getenv("BASE_URL") or f"http://127.0.0.1:{os.getenv('PORT', '8000')}"
DOCS_SERVICE_URL = f"{BASE_URL}/api/documents/retrieve"

SYSTEM_PROMPT = """
You are a specialized exam data extractor. You must determine the Subject, Level, Paper Number, and Year by analyzing the 'Latest Message' in the context of the 'Conversation History'.

CRITICAL INSTRUCTIONS:
1. ALWAYS prioritize the 'Latest Message'. If the user changes their mind (e.g., they previously asked for Paper 3 but now say "actually Paper 2"), you MUST update the value to the most recent request.
2. Review the 'Conversation History' to fill in gaps, but never let old data override a new, conflicting instruction from the user.
3. SUBJECT STANDARDIZATION: Convert any shorthand or slang subjects into their full formal names (e.g., 'maths' or 'math' becomes 'Mathematics', 'physics' becomes 'Physics', 'chem' becomes 'Chemistry', 'bio' becomes 'Biology', 'econ' becomes 'Economics').
4. For 'Level', convert 'a', 'o', 'a level', or 'o level' into the strictly formatted "A-Level" or "O-Level" (include the hyphen).
5. For 'Paper Number', always convert digits to words (e.g., 2 becomes TWO).

REQUIRED FIELDS:
1. Subject (Full formal name, e.g., Mathematics)
2. Level ("A-Level" or "O-Level")
3. Paper Number (e.g., ONE, TWO, THREE, or FOUR)
4. Year (e.g., 2024)

RESPONSE FORMAT (JSON ONLY):
- If all 4 are present and current: 
  {"status": "complete", "subject": "...", "year": "...", "paper_number": "...", "level": "..."}
- If info is missing:
  {"status": "incomplete", "message": "I need a few more details... [ask specifically for missing fields]"}

Analyze the history to be current, then act on the latest intent.
"""

class QueryRequest(BaseModel):
    user_id: str
    query: str

@router.post("")
async def extract_and_retrieve(request: QueryRequest):
    session_key = f"extract:session:{request.user_id}"
    
    try:
        # 1. Fetch Context
        existing_history = redis_client.get(session_key) or ""
        current_context = f"{existing_history}\nUser: {request.query}"
        
        # 2. Call Qwen via OpenRouter with JSON mode.
        response = openrouter_client.chat.completions.create(
            model=GEMINI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": current_context},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )

        # 3. Parse JSON safely
        raw = response.choices[0].message.content if response.choices else "{}"
        data = json.loads(raw or "{}")
        
        if data.get("status") == "complete":
            # Clear the session as extraction is finished
            redis_client.delete(session_key)
            
            # 4. Search parameters for your retrieval service
            search_params = {
                "subject": data.get('subject', '').upper(),
                "curriculum": "GCE",
                "paper_number": str(data.get('paper_number', '')).upper(),
                "year": str(data.get('year', '')),
                "level": data.get('level', '').upper()
            }

            async with httpx.AsyncClient(timeout=10.0) as http_client:
                doc_response = await http_client.get(DOCS_SERVICE_URL, params=search_params)
                
                if doc_response.status_code == 200:
                    retrieval_data = doc_response.json()
                    url = retrieval_data.get("file_url")
                    
                    return {
                        "status": "success",
                        "message": f"Found it! Here is your {data['subject']} {data['year']} Paper {data['paper_number']}.",
                        "file_url": url,
                        "metadata": data
                    }
                else:
                    return {
                        "status": "not_found",
                        "message": "I have the details, but I couldn't find a matching paper in the library.",
                        "metadata": data
                    }
        
        else:
            # 5. Handle Incomplete Info
            # Save history so the user can just reply with the missing part (e.g., "2024")
            redis_client.setex(session_key, 600, current_context)
            return {
                "status": "pending",
                "message": data.get("message", "Could you provide the year or paper number?"),
                "metadata": data
            }

    except Exception as e:
        return {"status": "error", "message": "I hit a snag processing that request.", "details": str(e)}