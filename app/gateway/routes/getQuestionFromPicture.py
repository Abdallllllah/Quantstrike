import json
from fastapi import APIRouter, UploadFile, File
from dotenv import load_dotenv, find_dotenv
from app.gateway.routes.llm_clients import openrouter_client, image_data_url, GEMINI_MODEL

load_dotenv(find_dotenv())

router = APIRouter(prefix="/api/vision", tags=["Vision Extraction"])

MULTI_EXTRACTION_PROMPT = """
You are a Professional STEM OCR and Question Parser.
1. Clean the text: Remove all artifacts, noise, and garbled characters (like "¿", "fs", "Vs+u").
2. Standardize Symbols: Ensure physics/math formulas are readable.
3. Identify Questions: Separate distinct problems.
4. MCQ Detection: If a question has options (A, B, C, D), extract them into a structured list.

Strictly respond in JSON:
{
  "full_raw_text": "Cleaned version of the full text",
  "questions": [
    {
      "id": 1,
      "type": "mcq" or "theory",
      "title": "Short title",
      "content": "The main question text",
      "options": ["Option A text", "Option B text", "Option C text", "Option D text"],
      "hint": "Brief context if needed"
    }
  ]
}
"""

@router.post("/get-questions")
async def get_questions(file: UploadFile = File(...)):
    try:
        image_bytes = await file.read()
        mime = file.content_type or "image/jpeg"

        response = openrouter_client.chat.completions.create(
            model=GEMINI_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": MULTI_EXTRACTION_PROMPT},
                        {"type": "image_url", "image_url": {"url": image_data_url(image_bytes, mime)}},
                    ],
                }
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content if response.choices else "{}"
        result = json.loads(raw or "{}")
        return {"status": "success", "data": result}

    except Exception as e:
        return {"status": "error", "message": str(e)}
