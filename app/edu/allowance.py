"""
Daily allowances and the top-up wallet (FR-S1, FR-S2, FR-S5, LLM-6).

All counting happens in Postgres (reg_consume_allowance), so it is server-side,
atomic and idempotent: a free unit is always used before a season pass or a
paid credit, raised caps are subject-scoped, and retrying the same request
(same ref, same WAT day) never charges twice.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.edu import config
from app.edu.db import db, one, rpc

WAT = timezone(timedelta(hours=1))


def wat_now() -> datetime:
    return datetime.now(WAT)


def next_reset_iso() -> str:
    """Next 00:00 WAT, as an ISO timestamp."""
    now = wat_now()
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _caps(kind: str):
    if kind == "question":
        return config.FREE_QUESTIONS_PER_DAY, config.TRACK_QUESTIONS_PER_DAY
    return config.FREE_PAPERS_PER_DAY, config.TRACK_PAPERS_PER_DAY


def raised_cap_subject_ids(user_id: str) -> list:
    """Subjects with an active, unexpired paid track (they get the raised cap)."""
    today = wat_now().date().isoformat()
    rows = (
        db().table("reg_enrolments").select("reg_tracks(subject_id,exam_date)")
        .eq("student_id", user_id).eq("status", "active").execute().data
    ) or []
    out = set()
    for r in rows:
        t = r.get("reg_tracks") or {}
        if t.get("subject_id") and (t.get("exam_date") or "") >= today:
            out.add(t["subject_id"])
    return sorted(out)


def status(user_id: str) -> dict:
    """Everything the "11 of 15 left today" counter and the cap screen need."""
    today = wat_now().date().isoformat()
    usage = one(db().table("reg_daily_usage").select("questions_used,papers_used")
                .eq("user_id", user_id).eq("day", today)) or {}
    wallet = one(db().table("reg_wallets").select("question_credits,paper_credits,season_pass_until")
                 .eq("user_id", user_id)) or {}
    pass_until = wallet.get("season_pass_until")
    q_used, p_used = usage.get("questions_used", 0), usage.get("papers_used", 0)
    return {
        "day": today,
        "resets_at": next_reset_iso(),
        "questions": {"used": q_used, "cap": config.FREE_QUESTIONS_PER_DAY,
                      "left": max(0, config.FREE_QUESTIONS_PER_DAY - q_used),
                      "raised_cap": config.TRACK_QUESTIONS_PER_DAY},
        "papers": {"used": p_used, "cap": config.FREE_PAPERS_PER_DAY,
                   "left": max(0, config.FREE_PAPERS_PER_DAY - p_used),
                   "raised_cap": config.TRACK_PAPERS_PER_DAY},
        "raised_cap_subject_ids": raised_cap_subject_ids(user_id),
        "credits": {"questions": wallet.get("question_credits", 0), "papers": wallet.get("paper_credits", 0)},
        "season_pass_until": pass_until,
        "season_pass_active": bool(pass_until and pass_until >= today),
    }


def can_spend(user_id: str, kind: str, subject_id: Optional[str]) -> bool:
    """Cheap pre-check before doing expensive work. The real decision is
    consume(); this only avoids starting an answer the student can't have."""
    s = status(user_id)
    block = s["questions"] if kind == "question" else s["papers"]
    cap = block["raised_cap"] if subject_id and subject_id in s["raised_cap_subject_ids"] else block["cap"]
    if block["used"] < cap or s["season_pass_active"]:
        return True
    return (s["credits"]["questions"] if kind == "question" else s["credits"]["papers"]) > 0


def consume(user_id: str, kind: str, ref: str, subject_id: Optional[str] = None) -> dict:
    """Spend one unit. Returns {allowed, source, used, cap, already}."""
    free_cap, raised_cap = _caps(kind)
    rows = rpc("reg_consume_allowance", {
        "p_user": user_id, "p_kind": kind, "p_ref": ref, "p_subject": subject_id,
        "p_free_cap": free_cap, "p_raised_cap": raised_cap,
    }) or [{}]
    r = rows[0]
    return {"allowed": bool(r.get("allowed")), "source": r.get("source"), "used": r.get("used", 0),
            "cap": r.get("cap", free_cap), "already": bool(r.get("already"))}


def cap_payload(kind: str) -> dict:
    """What the client shows at the cap: the reset time and the top-up offer."""
    noun = "questions" if kind == "question" else "past papers"
    cap = config.FREE_QUESTIONS_PER_DAY if kind == "question" else config.FREE_PAPERS_PER_DAY
    pack = "question_pack" if kind == "question" else "paper_pack"
    return {
        "code": "daily_cap_reached",
        "kind": kind,
        "message": f"You've used today's {cap} free {noun}. They reset at midnight (WAT).",
        "resets_at": next_reset_iso(),
        "offers": [dict(code=c, **p) for c, p in config.PRODUCTS.items() if p["type"] in (pack, "season_pass")],
    }
