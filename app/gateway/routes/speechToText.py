from fastapi import APIRouter, UploadFile, File
from app.gateway.routes.llm_clients import groq_client, WHISPER_MODEL

router = APIRouter(prefix="/api/audio", tags=["Speech Extraction"])


@router.post("/transcribe")
async def transcribe_speech(file: UploadFile = File(...)):
    try:
        audio_bytes = await file.read()
        name = file.filename or "audio.webm"
        mime = file.content_type or "audio/webm"

        # Groq Whisper expects an OpenAI-style file tuple: (filename, bytes, mime).
        result = groq_client.audio.transcriptions.create(
            model=WHISPER_MODEL,
            file=(name, audio_bytes, mime),
            response_format="text",
        )

        # response_format='text' returns the raw transcript string.
        transcript = result if isinstance(result, str) else getattr(result, "text", "")
        return {"status": "success", "data": {"transcript": transcript.strip()}}

    except Exception as e:
        return {"status": "error", "message": str(e)}
