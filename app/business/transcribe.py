"""
Voice transcription for the business (Tara) section.

Uses Groq Whisper (whisper-large-v3) — a purpose-built speech-to-text model. It
is reliable, fast, handles French / English / Pidgin / Swahili and African
accents, and — unlike a chat model — NEVER "refuses" to transcribe. Reuses the
gateway's shared Groq client (GROQ_API_KEY).
"""


async def transcribe_audio(data: bytes, filename: str = "audio.wav", content_type: str = "audio/wav") -> str:
    """Transcribe raw audio bytes to text. Returns '' on empty/failure so the
    caller can ask the shopkeeper to record again instead of feeding garbage
    into the parser."""
    if not data:
        return ""
    from app.gateway.routes.llm_clients import async_groq_client, WHISPER_MODEL

    # A short vocabulary hint biases Whisper toward shop-commerce words + FCFA.
    hint = (
        "Provision-shop commerce in Cameroon. Money in FCFA. Languages: French, "
        "English, Cameroonian Pidgin, Swahili. Keep quantities and amounts as digits."
    )
    result = await async_groq_client.audio.transcriptions.create(
        model=WHISPER_MODEL,
        file=(filename or "audio.wav", data, content_type or "audio/wav"),
        response_format="text",
        prompt=hint,
    )
    if isinstance(result, str):
        return result.strip()
    return (getattr(result, "text", "") or "").strip()
