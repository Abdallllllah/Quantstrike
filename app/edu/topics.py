"""
Syllabus topics and automatic tagging (FR-S1 "every answer is tagged",
FR-S3 profile). Until admins load the official syllabus, the tagger proposes
topic names; they are stored as unverified and reused for later questions, so
the per-student map builds up from day one.
"""
import re
import time
from typing import Optional

from app.edu import config, llm
from app.edu.db import db, one

_subjects: dict = {}
_subjects_at = 0.0


def subjects() -> list:
    """Launch subjects (principle 6), in a stable order."""
    global _subjects_at
    if not _subjects or time.monotonic() - _subjects_at > 600:
        rows = db().table("reg_subjects").select("id,name,slug").eq("is_active", True).execute().data or []
        _subjects.clear()
        _subjects.update({r["slug"]: r for r in rows})
        _subjects_at = time.monotonic()
    return [_subjects[s] for s in config.LAUNCH_SUBJECT_SLUGS if s in _subjects]


def subject_by_any(value: Optional[str]) -> Optional[dict]:
    """Find a launch subject by id, slug or (loose) name: 'maths', 'Bio', 'Physics'."""
    if not value:
        return None
    v = value.strip().lower()
    for s in subjects():
        name = s["name"].lower()
        if v in (s["id"], s["slug"], name) or name.startswith(v[:4]) or v.startswith(s["slug"][:4]):
            return s
    return None


def topic_tree(subject_id: str) -> list:
    """[{id, name, code, verified, subtopics:[...]}] for one subject."""
    rows = (db().table("reg_syllabus_topics").select("id,parent_id,name,code,sort,verified")
            .eq("subject_id", subject_id).order("sort").order("name").execute().data) or []
    tops = [dict(r, subtopics=[]) for r in rows if not r["parent_id"]]
    by_id = {t["id"]: t for t in tops}
    for r in rows:
        if r["parent_id"] in by_id:
            by_id[r["parent_id"]]["subtopics"].append(r)
    return tops


def get_or_create_topic(subject_id: str, name: str, parent_id: Optional[str] = None) -> Optional[str]:
    name = re.sub(r"\s+", " ", (name or "").strip())[:120]
    if not name or name.lower() in ("null", "none", "general"):
        return None

    def find():
        q = db().table("reg_syllabus_topics").select("id").eq("subject_id", subject_id).ilike("name", name)
        q = q.eq("parent_id", parent_id) if parent_id else q.is_("parent_id", "null")
        return one(q)

    row = find()
    if row:
        return row["id"]
    try:
        return db().table("reg_syllabus_topics").insert({
            "subject_id": subject_id, "parent_id": parent_id, "name": name, "verified": False,
        }).execute().data[0]["id"]
    except Exception:  # noqa: BLE001 — lost a race to the unique index
        row = find()
        return row["id"] if row else None


_TAG_PROMPT = """You classify one message from a Cameroon GCE Advanced Level (Anglophone) student.
Subjects: {subjects}.
Known topics (reuse these exact names when they fit):
{topics}

Reply with JSON only, no prose:
{{"academic": true or false,   (false for greetings, small talk, and anything off the GCE syllabus)
  "subject": "one of the subject names above, or null",
  "topic": "short syllabus topic name in Title Case, or null",
  "subtopic": "short sub-topic name, or null"}}"""


async def tag(text: str, hint_subject_id: Optional[str] = None) -> dict:
    """Return {academic, subject_id, topic_id, subtopic_id}. Never raises:
    tagging must never block an answer."""
    out = {"academic": True, "subject_id": hint_subject_id, "topic_id": None, "subtopic_id": None}
    text = (text or "").strip()
    if not text:
        return out
    try:
        subs = subjects()
        known = []
        for s in subs:
            names = [t["name"] for t in topic_tree(s["id"])][:40]
            if names:
                known.append(f"{s['name']}: " + "; ".join(names))
        data = await llm.complete_json([
            {"role": "system", "content": _TAG_PROMPT.format(
                subjects=", ".join(s["name"] for s in subs), topics="\n".join(known) or "(none yet)")},
            {"role": "user", "content": text[:2000]},
        ], model=config.TAGGING_MODEL, max_tokens=200, timeout=20)
        if not isinstance(data, dict):
            return out
        out["academic"] = bool(data.get("academic", True))
        subject = subject_by_any(data.get("subject")) or subject_by_any(hint_subject_id)
        if not subject or not out["academic"]:
            return out
        out["subject_id"] = subject["id"]
        out["topic_id"] = get_or_create_topic(subject["id"], data.get("topic") or "")
        if out["topic_id"] and data.get("subtopic"):
            out["subtopic_id"] = get_or_create_topic(subject["id"], data["subtopic"], parent_id=out["topic_id"])
    except Exception as e:  # noqa: BLE001
        print(f"tagging skipped: {e}")
    return out
