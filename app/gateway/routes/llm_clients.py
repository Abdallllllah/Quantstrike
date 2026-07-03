"""
Shared OpenAI-compatible clients for OpenRouter (Gemini 1.5) and Groq (Whisper).

OpenRouter handles all text + vision work using Gemini 1.5 Flash. The model is
called via OpenRouter's OpenAI-compatible API — we authenticate with
OPENROUTER_API_KEY only; the GEMINI_API_KEY is intentionally NOT used.

Groq Whisper handles audio transcription, since OpenRouter's chat completions
endpoint does not currently accept audio input parts.

Models are env-overridable so we can swap variants without touching code.
"""
import base64
import os
from openai import AsyncOpenAI, OpenAI
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

# --- Model defaults --------------------------------------------------------
# Gemini 2.5 Flash via OpenRouter — handles text + image, generous context,
# strong quality for the price. One model id covers chat routing, image OCR,
# and PDF page OCR. Override via env to swap variants without code changes.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "google/gemini-2.5-flash")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-large-v3")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# Optional headers OpenRouter recommends for analytics & rate-limit perks.
_OPENROUTER_HEADERS = {
    "HTTP-Referer": os.getenv("OPENROUTER_REFERER", "https://quantstrikecontroller.onrender.com"),
    "X-Title": os.getenv("OPENROUTER_APP_TITLE", "QuantstrikeController"),
}


# --- Clients ---------------------------------------------------------------
openrouter_client = OpenAI(
    base_url=OPENROUTER_BASE_URL,
    api_key=os.getenv("OPENROUTER_API_KEY"),
    default_headers=_OPENROUTER_HEADERS,
)

async_openrouter_client = AsyncOpenAI(
    base_url=OPENROUTER_BASE_URL,
    api_key=os.getenv("OPENROUTER_API_KEY"),
    default_headers=_OPENROUTER_HEADERS,
)

groq_client = OpenAI(
    base_url=GROQ_BASE_URL,
    api_key=os.getenv("GROQ_API_KEY"),
)

async_groq_client = AsyncOpenAI(
    base_url=GROQ_BASE_URL,
    api_key=os.getenv("GROQ_API_KEY"),
)


# --- Helpers ---------------------------------------------------------------
def image_data_url(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    """Encode raw image bytes as a base64 data URL for the OpenAI vision API."""
    if not mime_type or not mime_type.startswith("image/"):
        mime_type = "image/jpeg"
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{b64}"
