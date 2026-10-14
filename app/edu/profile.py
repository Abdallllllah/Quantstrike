"""
Student profile — the syllabus map (FR-S3). Per student, per subject, per
topic: questions asked, attempts, accuracy, "still unsure" flags and last
activity, shown as a heat map. It is also the hand-off object to a teacher
track (the cohort view reads the same data).

States:
  untouched — no activity on the topic
  weak      — any "still unsure", or attempt accuracy below 50%
  strong    — attempted with accuracy of 70% or more and nothing unsure
  learning  — asked about but not yet tested (or accuracy 50–69%)
"""
from typing import Optional

from app.edu import topics
from app.edu.db import db

WEAK_BELOW = 0.5
STRONG_FROM = 0.7


def state_of(stat: Optional[dict]) -> str:
    if not stat:
        return "untouched"
    total = float(stat.get("marks_total") or 0)
    acc = (float(stat.get("marks_awarded") or 0) / total) if total else None
    if (stat.get("unsure_count") or 0) > 0 or (acc is not None and acc < WEAK_BELOW):
        return "weak"
    if acc is not None and acc >= STRONG_FROM and (stat.get("attempted") or 0) > 0:
        return "strong"
    return "learning"


def _accuracy(stat: dict) -> Optional[float]:
    total = float(stat.get("marks_total") or 0)
    return round(float(stat.get("marks_awarded") or 0) / total, 3) if total else None


def subject_map(user_id: str, subject_id: str) -> dict:
    stats = {s["topic_id"]: s for s in (db().table("reg_topic_stats").select("*")
                                       .eq("user_id", user_id).eq("subject_id", subject_id)
                                       .execute().data or [])}
    tree = topics.topic_tree(subject_id)
    rows = []
    counts = {"strong": 0, "weak": 0, "learning": 0, "untouched": 0}
    for t in tree:
        st = stats.get(t["id"])
        state = state_of(st)
        counts[state] += 1
        rows.append({
            "topic_id": t["id"], "name": t["name"], "code": t.get("code"), "verified": t.get("verified"),
            "state": state,
            "asked": (st or {}).get("asked", 0), "attempted": (st or {}).get("attempted", 0),
            "accuracy": _accuracy(st) if st else None, "unsure_count": (st or {}).get("unsure_count", 0),
            "last_activity": (st or {}).get("last_activity"),
        })
    covered = sum(1 for r in rows if r["state"] != "untouched")
    return {
        "subject_id": subject_id,
        "coverage": round(covered / len(rows), 3) if rows else 0.0,
        "counts": counts,
        "topics": sorted(rows, key=lambda r: ({"weak": 0, "learning": 1, "strong": 2, "untouched": 3}[r["state"]], r["name"])),
        "last_activity": max((r["last_activity"] for r in rows if r["last_activity"]), default=None),
    }


def full_profile(user: dict) -> dict:
    """Heat map for every launch subject the student studies (all, if none chosen)."""
    chosen = set(user.get("subjects") or [])
    subjects = [s for s in topics.subjects() if not chosen or s["slug"] in chosen]
    out = []
    for s in subjects:
        m = subject_map(user["id"], s["id"])
        out.append({"subject": s, **m})
    return {"subjects": out}


def weak_subject_ids(user_id: str) -> list:
    """Subjects where the student has at least one weak topic (FR-C2)."""
    rows = db().table("reg_topic_stats").select("subject_id,unsure_count,marks_awarded,marks_total,attempted") \
        .eq("user_id", user_id).execute().data or []
    return sorted({r["subject_id"] for r in rows if state_of(r) == "weak"})
