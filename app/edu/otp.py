"""
Phone + OTP sign-in (FR-S4). Codes are 6 digits, stored hashed, expire after
CARATI_OTP_TTL seconds and allow CARATI_OTP_MAX_ATTEMPTS tries.

SMS delivery is pluggable. With the default "log" provider the code is only
written to the server log, which is why OTP is optional
(CARATI_OTP_REQUIRED=false) until a real SMS provider is added here.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from app.edu import config
from app.edu.auth import SECRET_KEY
from app.edu.db import db, one


def _hash(phone: str, code: str) -> str:
    return hmac.new(SECRET_KEY.encode(), f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


def send_sms(phone: str, text: str) -> None:
    if config.SMS_PROVIDER == "log":
        print(f"[OTP] {phone}: {text}")
        return
    raise RuntimeError(f"SMS provider '{config.SMS_PROVIDER}' is not implemented")


def request_code(phone: str) -> dict:
    recent = one(db().table("reg_otp_codes").select("created_at").eq("phone", phone)
                 .order("created_at", desc=True))
    now = datetime.now(timezone.utc)
    if recent and (now - datetime.fromisoformat(recent["created_at"])).total_seconds() < 30:
        raise ValueError("Please wait 30 seconds before asking for another code")
    code = f"{secrets.randbelow(1_000_000):06d}"
    db().table("reg_otp_codes").insert({
        "phone": phone, "code_hash": _hash(phone, code),
        "expires_at": (now + timedelta(seconds=config.OTP_TTL_SECONDS)).isoformat(),
    }).execute()
    send_sms(phone, f"Your Carati code is {code}. It expires in {config.OTP_TTL_SECONDS // 60} minutes.")
    return {"sent": True, "expires_in": config.OTP_TTL_SECONDS}


def verify_code(phone: str, code: str) -> bool:
    row = one(db().table("reg_otp_codes").select("*").eq("phone", phone).is_("consumed_at", "null")
              .order("created_at", desc=True))
    if not row:
        return False
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc) or row["attempts"] >= config.OTP_MAX_ATTEMPTS:
        return False
    if not hmac.compare_digest(row["code_hash"], _hash(phone, (code or "").strip())):
        db().table("reg_otp_codes").update({"attempts": row["attempts"] + 1}).eq("id", row["id"]).execute()
        return False
    db().table("reg_otp_codes").update({"consumed_at": datetime.now(timezone.utc).isoformat()}).eq("id", row["id"]).execute()
    return True
