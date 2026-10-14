"""
The one interface to the language model (implementation note: the provider is
swappable; prompts and corpus are Carati's). Today it calls OpenRouter's
OpenAI-compatible API; swap the client here and nothing else changes.
"""
import json
import re
from typing import Optional

from app.edu import config

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _client():
    from app.gateway.routes.llm_clients import async_openrouter_client
    return async_openrouter_client


async def complete(messages: list, *, model: Optional[str] = None, max_tokens: Optional[int] = None,
                   temperature: float = 0.3, timeout: float = 60) -> str:
    resp = await _client().chat.completions.create(
        model=model or config.EXPLAIN_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    return (resp.choices[0].message.content or "").strip() if resp.choices else ""


async def complete_json(messages: list, *, model: Optional[str] = None, max_tokens: int = 1500,
                        temperature: float = 0.1, timeout: float = 60):
    """Ask for JSON and parse it. Returns None if the model didn't comply."""
    resp = await _client().chat.completions.create(
        model=model or config.TAGGING_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        response_format={"type": "json_object"},
    )
    raw = (resp.choices[0].message.content or "").strip() if resp.choices else ""
    try:
        return json.loads(_FENCE.sub("", raw))
    except (ValueError, TypeError):
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                return None
        return None


def image_part(data_url: str) -> dict:
    return {"type": "image_url", "image_url": {"url": data_url}}
