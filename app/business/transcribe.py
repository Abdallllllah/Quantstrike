"""
Voice transcription for the business (Tara) section — ported from
tara/src/server/transcribe-core.js. Uses OpenAI's audio model on OpenRouter,
grounded in African provision-shop / commerce speech (FR / EN / Pidgin / Swahili,
FCFA amounts). Transcribes VERBATIM; no translation.
"""
import os
import base64
import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_AUDIO_MODEL = "openai/gpt-audio"  # cheaper: openai/gpt-audio-mini

_PROMPT = (
    "You are a speech-to-text engine for a WhatsApp assistant used by provision-shop keepers "
    "and small commercial traders in AFRICA — Cameroon and the wider CEMAC / francophone-African "
    "markets. Speakers have African accents and talk about everyday commerce: buying, selling, "
    "restocking, customer debts, and money in FCFA / CFA francs. Transcribe the audio VERBATIM in "
    "the language(s) actually spoken (French, English, Kiswahili, Cameroonian Pidgin — often "
    "code-switched). Do NOT translate, normalise, summarise or correct. Preserve quantities and "
    "money amounts exactly as said (e.g. '4 kg', '2600', 'FCFA'); write digits as digits. Return "
    "ONLY the raw transcription text. If the audio is silent or unintelligible, return an empty string."
)


async def transcribe_audio(data: bytes, fmt: str = "wav") -> str:
    """Transcribe raw audio bytes to text. Returns '' on empty/failure."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set on the server.")
    model = os.getenv("BUSINESS_AUDIO_MODEL") or os.getenv("OPENROUTER_MODEL") or DEFAULT_AUDIO_MODEL
    b64 = base64.b64encode(data).decode()

    payload = {
        "model": model,
        "temperature": 0,
        "modalities": ["text"],
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": _PROMPT},
                {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}},
            ],
        }],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "https://quantstrike.local"),
        "X-Title": "Quantstrike business",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(OPENROUTER_URL, json=payload, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"OpenRouter {resp.status_code}: {resp.text[:400]}")

    data_json = resp.json()
    content = (data_json.get("choices") or [{}])[0].get("message", {}).get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(p if isinstance(p, str) else (p.get("text") or "") for p in content).strip()
    return ""


def guess_audio_format(filename: str, content_type: str) -> str:
    """Map a filename/mime to the audio format string the model expects."""
    name = (filename or "").lower()
    ct = (content_type or "").lower()
    for ext in ("wav", "mp3", "m4a", "ogg", "webm", "flac", "aac"):
        if name.endswith("." + ext) or ext in ct:
            return "mp3" if ext in ("m4a", "aac") else ext
    return "wav"
