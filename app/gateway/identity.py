"""
One student identity for the WhatsApp gateway and the Carati app.

The gateway used to keep its own `users` table. Students now live only in
reg_users (keyed by phone); old gateway ids are kept in reg_users.legacy_user_id
so history written under them (messages, practice_sessions) is still found.
"""
import re
from typing import Optional

from app.edu.db import db, general_school_id, one
from app.edu.phone import normalize_phone

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def resolve_student(raw_id: str, create: bool = True) -> Optional[dict]:
    """Accepts a reg_users id, an old gateway users.id, or a phone number.
    Unknown phone numbers get a student account (create=True)."""
    raw = (raw_id or "").strip()
    if not raw:
        return None
    try:
        if UUID_RE.match(raw):
            return (one(db().table("reg_users").select("*").eq("id", raw))
                    or one(db().table("reg_users").select("*").eq("legacy_user_id", raw)))
        phone = normalize_phone(raw)
        if not phone:
            return None
        u = one(db().table("reg_users").select("*").eq("phone_number", phone))
        if u or not create:
            return u
        return db().table("reg_users").insert(
            {"phone_number": phone, "role": "student", "school_id": general_school_id()}).execute().data[0]
    except Exception as e:  # noqa: BLE001
        print(f"Student lookup failed for {raw!r}: {e}")
        return None


def resolve_student_id(raw_id: str) -> Optional[str]:
    u = resolve_student(raw_id)
    return u["id"] if u else None


def all_ids(student: dict) -> list:
    """Ids this student's gateway history may be stored under."""
    return [i for i in (student.get("id"), student.get("legacy_user_id")) if i]
