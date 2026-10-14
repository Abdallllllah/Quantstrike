"""
Carati product settings (Requirements v0.2). Everything a product decision may
change — caps, prices, launch subjects, models — lives here and can be
overridden with environment variables, so tuning never needs a code change.
"""
import os
from datetime import date


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# --- Free tier (FR-S1 / FR-S2) — never reduce these below the requirement ----
FREE_QUESTIONS_PER_DAY = _int("CARATI_FREE_QUESTIONS", 15)
FREE_PAPERS_PER_DAY = _int("CARATI_FREE_PAPERS", 2)
# Raised caps for the subject of an active paid track (FR-S5).
TRACK_QUESTIONS_PER_DAY = _int("CARATI_TRACK_QUESTIONS", 30)
TRACK_PAPERS_PER_DAY = _int("CARATI_TRACK_PAPERS", 4)
# Follow-ups inside one thread that don't count as a new question.
FREE_FOLLOW_UPS = _int("CARATI_FREE_FOLLOW_UPS", 3)

# --- Launch scope (principle 6) ---------------------------------------------
LAUNCH_SUBJECT_SLUGS = [
    s.strip() for s in os.getenv(
        "CARATI_LAUNCH_SUBJECTS", "physics,chemistry,math,biology,economics"
    ).split(",") if s.strip()
]

# --- Top-up catalogue (FR-S5). Prices in FCFA are PLACEHOLDERS until the
# parent interviews fix them (open decision 3). Override with env vars.
PRODUCTS = {
    "questions_10": {"type": "question_pack", "quantity": 10, "price_fcfa": _int("CARATI_PRICE_Q10", 200), "label": "10 extra questions"},
    "questions_25": {"type": "question_pack", "quantity": 25, "price_fcfa": _int("CARATI_PRICE_Q25", 450), "label": "25 extra questions"},
    "questions_50": {"type": "question_pack", "quantity": 50, "price_fcfa": _int("CARATI_PRICE_Q50", 800), "label": "50 extra questions"},
    "papers_5": {"type": "paper_pack", "quantity": 5, "price_fcfa": _int("CARATI_PRICE_P5", 250), "label": "5 extra past papers"},
    "papers_15": {"type": "paper_pack", "quantity": 15, "price_fcfa": _int("CARATI_PRICE_P15", 600), "label": "15 extra past papers"},
    "season_pass": {"type": "season_pass", "quantity": 0, "price_fcfa": _int("CARATI_PRICE_SEASON", 5000), "label": "Season Pass (no daily limits until the June GCE)"},
}

# --- GCE calendar -----------------------------------------------------------
# The June GCE. Exam year is inferred from level; the Season Pass runs until it.
GCE_EXAM_MONTH = _int("CARATI_GCE_MONTH", 6)
GCE_EXAM_LAST_DAY = _int("CARATI_GCE_LAST_DAY", 30)


def season_end(today: date) -> date:
    """Last day of the next June GCE on or after `today`."""
    year = today.year if today <= date(today.year, GCE_EXAM_MONTH, GCE_EXAM_LAST_DAY) else today.year + 1
    return date(year, GCE_EXAM_MONTH, GCE_EXAM_LAST_DAY)


def infer_exam_year(level: str, today: date) -> int:
    """Upper Sixth sits the next June GCE; Lower Sixth the one after."""
    year = season_end(today).year
    return year + 1 if level == "lower_sixth" else year


# --- Login (FR-S4) ----------------------------------------------------------
# Phone + OTP. Until an SMS provider is configured, OTP is off and login is by
# phone alone (codes, if requested, are written to the server log).
OTP_REQUIRED = _bool("CARATI_OTP_REQUIRED", False)
OTP_TTL_SECONDS = _int("CARATI_OTP_TTL", 300)
OTP_MAX_ATTEMPTS = _int("CARATI_OTP_MAX_ATTEMPTS", 5)
SMS_PROVIDER = os.getenv("CARATI_SMS_PROVIDER", "log")  # log | (add a provider in app/edu/sms.py)

# --- LLM (LLM-6: cheaper model for marking and tagging) ---------------------
EXPLAIN_MODEL = os.getenv("CARATI_EXPLAIN_MODEL", os.getenv("GEMINI_MODEL", "google/gemini-2.5-flash"))
MARKING_MODEL = os.getenv("CARATI_MARKING_MODEL", "google/gemini-2.5-flash-lite")
TAGGING_MODEL = os.getenv("CARATI_TAGGING_MODEL", "google/gemini-2.5-flash-lite")
ANSWER_MAX_TOKENS = _int("CARATI_ANSWER_MAX_TOKENS", 900)
# LLM-1: when true, syllabus questions with no supporting material in the corpus
# are not answered from general knowledge. Off until the corpus covers the syllabus.
STRICT_GROUNDING = _bool("CARATI_STRICT_GROUNDING", False)

# --- Teacher tracks (FR-T3) -------------------------------------------------
TEACHER_MARKED_PER_WEEK = _int("CARATI_TEACHER_MARKS_PER_WEEK", 2)
REFUND_WINDOW_DAYS = _int("CARATI_REFUND_DAYS", 7)
REFUND_MAX_STEPS_DONE = _int("CARATI_REFUND_MAX_STEPS", 1)  # "fewer than two steps"
