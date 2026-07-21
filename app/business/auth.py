"""
Phone-based auth for the business (Tara) section. Self-contained so it never
touches the school app. Login is the phone number (no password); tokens are
HMAC-signed, expiring envelopes around the biz_user id.
"""
import os
import hmac
import json
import base64
import hashlib
import time
from typing import Optional

from fastapi import Header, HTTPException, Depends

SECRET_KEY = os.getenv("BUSINESS_SECRET_KEY", os.getenv("EDU_SECRET_KEY", "dev-insecure-change-me"))
TOKEN_TTL_SECONDS = int(os.getenv("BUSINESS_TOKEN_TTL", str(60 * 60 * 24 * 60)))  # 60 days


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload_b64: str) -> str:
    return _b64e(hmac.new(SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).digest())


def make_token(user_id: str) -> str:
    payload = {"uid": str(user_id), "kind": "biz", "exp": int(time.time()) + TOKEN_TTL_SECONDS}
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    return f"{payload_b64}.{_sign(payload_b64)}"


def decode_token(token: str) -> Optional[dict]:
    try:
        payload_b64, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(payload_b64)):
        return None
    try:
        payload = json.loads(_b64d(payload_b64))
    except Exception:
        return None
    if payload.get("kind") != "biz" or int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


def get_current_business_user(authorization: str = Header(None)) -> dict:
    """Resolve the bearer token to a live biz_users row. 401 on any failure."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    payload = decode_token(authorization[7:].strip())
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    from app.db.supabase import get_supabase_client
    client = get_supabase_client().client
    res = client.table("biz_users").select("*").eq("id", payload["uid"]).limit(1).execute()
    if not res.data:
        raise HTTPException(status_code=401, detail="Account no longer exists")
    return res.data[0]
