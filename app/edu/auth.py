"""
Lightweight auth for the school/teacher/student app.

Login is name + phone number (no passwords), so tokens are just a signed,
expiring envelope around the user id + role. Signed with HMAC-SHA256 using
EDU_SECRET_KEY — stdlib only, no extra dependencies.
"""
import os
import hmac
import json
import base64
import hashlib
import time
from typing import Optional

from fastapi import Header, HTTPException, Depends

SECRET_KEY = os.getenv("EDU_SECRET_KEY", "dev-insecure-change-me")
TOKEN_TTL_SECONDS = int(os.getenv("EDU_TOKEN_TTL", str(60 * 60 * 24 * 30)))  # 30 days


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload_b64: str) -> str:
    sig = hmac.new(SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).digest()
    return _b64e(sig)


def make_token(user_id: str, role: str, school_id: Optional[str]) -> str:
    """Issue a signed token for a user."""
    payload = {
        "uid": str(user_id),
        "role": role,
        "school_id": str(school_id) if school_id else None,
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    return f"{payload_b64}.{_sign(payload_b64)}"


def decode_token(token: str) -> Optional[dict]:
    """Return the payload if the token is valid and unexpired, else None."""
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
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


# --- FastAPI dependencies ---------------------------------------------------

def get_current_user(authorization: str = Header(None)) -> dict:
    """Resolve the bearer token to a live reg_users row. 401 on any failure."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    payload = decode_token(authorization[7:].strip())
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    from app.db.supabase import get_supabase_client
    client = get_supabase_client().client
    res = client.table("reg_users").select("*").eq("id", payload["uid"]).limit(1).execute()
    if not res.data:
        raise HTTPException(status_code=401, detail="User no longer exists")
    return res.data[0]


def require_role(*roles: str):
    """Dependency factory: allow only the given roles."""
    def _dep(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail=f"Requires role: {', '.join(roles)}")
        return user
    return _dep
