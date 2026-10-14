"""
Question & answer — the core of the free tier (FR-S1, LLM-1..7).

Channel-agnostic: the app (/api/edu/chat) and WhatsApp both call ask(), so a
student has one allowance and one profile whichever way they ask.

Counting rules (FR-S1):
  * the first message of a thread is a question and counts;
  * the next CARATI_FREE_FOLLOW_UPS (3) messages in the same thread are free
    follow-ups; the one after that counts again, and so on;
  * "still unsure" re-explanations, cached answers and non-academic chat never count;
  * a follow-up is never refused, so a thread is never cut off mid-answer.
"""
import asyncio
import hashlib
import re
import uuid
from typing import Optional

from app.edu import allowance, config, llm, topics
from app.edu.db import db, general_school_id, one, rpc, run
from app.edu.retrieval import retrieve_context

SYSTEM_PROMPT = """You are Carati, a warm, encouraging study companion for Cameroon GCE Advanced Level students (Anglophone subsystem).

SCOPE. You help with the GCE A-Level syllabus. Detect the subject yourself; never ask the student to pick one. If a request is off the syllabus or not academic, decline in one kind sentence and steer back to their studies. Brief, friendly replies to greetings are fine.

ANSWER DIRECTLY. Never reply with a checklist of clarifying questions. Make a sensible assumption and answer. Ask at most one short question, only if you truly cannot proceed.

GCE STYLE. Align to the Cameroon GCE A-Level syllabus, its terminology and notation. Give the mark-earning answer.
- For a definition ("what is ..."), give just the definition unless asked for more.
- For problems, show the working step by step: data with units, formula, substitution, result to 3 significant figures with units.
- Be concise. No padding.

FORMAT. Plain text only: no markdown, no asterisks, no # headings, no bullet symbols, no LaTeX. Write maths with unicode (x², √, π, ×, ½, H₂O, →) and fractions inline as (a+b)/c. Describe diagrams in words.

SAFETY. Everything must suit students under 18. Decline anything harmful, dishonest or inappropriate, gently. Support honest revision; never help anyone cheat in a live exam."""

MATERIALS_NOTE = (
    "MATERIALS FROM CARATI'S GCE LIBRARY (syllabus, past questions, marking schemes, approved notes). "
    "Base your answer on them; they are the authoritative source. Quote definitions from them "
    "word for word; do not paraphrase. Do not mention that you were given materials."
)
NO_MATERIALS_NOTE = (
    "No passage from Carati's library matched this question. Answer from the GCE A-Level syllabus "
    "as it is examined in Cameroon."
)
STRICT_REFUSAL = (
    "I don't have material on that in the Carati library yet, so I won't guess. Try asking it a "
    "different way, or pick a past paper on this topic and we can work through a question together."
)
UNSURE_PROMPT = (
    "The student is still unsure after your last explanation. Explain it again more simply: "
    "shorter sentences, one idea at a time, an everyday example, and the key result at the end. "
    "Same plain-text rules."
)

_MD = [
    (re.compile(r"\*\*([^*\n]+?)\*\*"), r"\1"),
    (re.compile(r"__([^_\n]+?)__"), r"\1"),
    (re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)"), r"\1"),
    (re.compile(r"(?<!\w)_([^_\n]+?)_(?!\w)"), r"\1"),
    (re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE), ""),
    (re.compile(r"^(\s*)[*\-+]\s+", re.MULTILINE), r"\1• "),
    (re.compile(r"`([^`]+)`"), r"\1"),
]


def to_plain_text(text: str) -> str:
    """Strip the markdown the model keeps emitting despite instructions."""
    for pattern, repl in _MD:
        text = pattern.sub(repl, text or "")
    return text


def _clean(answer: str) -> str:
    from app.gateway.routes.text_cleanup import clean_math_notation
    return to_plain_text(clean_math_notation(answer))


def question_hash(text: str) -> str:
    norm = re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", (text or "").lower())).strip()
    return hashlib.sha256(norm.encode()).hexdigest()


def _count_cache_hit(qhash: str) -> None:
    from datetime import datetime, timezone
    row = one(db().table("reg_answer_cache").select("hits").eq("question_hash", qhash)) or {}
    db().table("reg_answer_cache").update({
        "hits": (row.get("hits") or 0) + 1, "last_hit_at": datetime.now(timezone.utc).isoformat(),
    }).eq("question_hash", qhash).execute()


class CapReached(Exception):
    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


# --------------------------------------------------------------------------
# Thread bookkeeping
# --------------------------------------------------------------------------
def _thread_user_turns(user_id: str, conversation_id: str) -> int:
    """How many questions/follow-ups the student has already sent in this thread
    (re-explanation requests excluded)."""
    rows = (db().table("reg_messages").select("id", count="exact")
            .eq("user_id", user_id).eq("conversation_id", conversation_id)
            .eq("role", "user").eq("is_reexplanation", False).execute())
    return rows.count or 0


def _history(user_id: str, conversation_id: str) -> list:
    rows = (db().table("reg_messages").select("role,content")
            .eq("user_id", user_id).eq("conversation_id", conversation_id)
            .order("created_at", desc=True).limit(12).execute().data) or []
    return list(reversed(rows))


def _existing_reply(user_id: str, client_ref: str) -> Optional[dict]:
    """Idempotency: a retried request (same client_ref) gets the answer already
    given, without another model call or another allowance charge."""
    return one(db().table("reg_messages").select("*")
               .eq("user_id", user_id).eq("client_ref", client_ref + ":a"))


def _save(user: dict, conversation_id: str, role: str, content: str, **cols) -> dict:
    row = {"user_id": user["id"], "school_id": user.get("school_id"), "role": role,
           "content": content, "conversation_id": conversation_id, **cols}
    return db().table("reg_messages").insert(row).execute().data[0]


# --------------------------------------------------------------------------
# Answering
# --------------------------------------------------------------------------
async def _answer(history: list, text: str, image_url: Optional[str], context: str,
                  extra_system: Optional[str] = None) -> str:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": (MATERIALS_NOTE + "\n\n" + context) if context else NO_MATERIALS_NOTE}]
    if extra_system:
        msgs.append({"role": "system", "content": extra_system})
    for h in history:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            msgs.append({"role": h["role"], "content": h["content"]})
    if image_url:
        msgs.append({"role": "user", "content": [
            {"type": "text", "text": text or "Please read this question and help me with it."},
            llm.image_part(image_url),
        ]})
    else:
        msgs.append({"role": "user", "content": text})
    raw = await llm.complete(msgs, max_tokens=config.ANSWER_MAX_TOKENS, temperature=0.1 if context else 0.4)
    return _clean(raw) or "I'm not sure how to answer that. Could you rephrase it?"


async def ask(user: dict, text: str, *, conversation_id: Optional[str] = None,
              image_url: Optional[str] = None, attachment_text: str = "", attachment_note: str = "",
              client_ref: Optional[str] = None, channel: str = "app") -> dict:
    """Answer one student message. Raises CapReached when a NEW question is
    asked with nothing left to spend."""
    uid = user["id"]
    text = (text or "").strip()
    if client_ref:
        prior = await run(_existing_reply, uid, client_ref)
        if prior:
            return _reply_payload(prior, conversation_id=prior["conversation_id"], replayed=True)

    conv = conversation_id or str(uuid.uuid4())
    prior_turns = await run(_thread_user_turns, uid, conv)
    is_new_problem = prior_turns % (config.FREE_FOLLOW_UPS + 1) == 0

    question_for_model = text + (("\n\n" + attachment_text) if attachment_text else "")
    tagging, context = await asyncio.gather(
        topics.tag(question_for_model or "(photo of a question)"),
        retrieve_context(general_school_id(), text) if text else asyncio.sleep(0, result=""),
    )
    academic = tagging["academic"] or bool(image_url)
    subject_id = tagging["subject_id"]

    counts = is_new_problem and academic
    cached_answer = None
    qhash = None
    if counts and prior_turns == 0 and text and not image_url and not attachment_text:
        qhash = question_hash(text)
        hit = await run(lambda: one(db().table("reg_answer_cache").select("answer,disabled")
                                    .eq("question_hash", qhash)))
        if hit and not hit.get("disabled"):
            cached_answer = hit["answer"]

    if counts and cached_answer is None:
        ok = await run(allowance.can_spend, uid, "question", subject_id)
        if not ok:
            raise CapReached("question")

    # --- produce the answer ---
    if cached_answer is not None:
        answer, cached = cached_answer, True
        await run(_count_cache_hit, qhash)
    elif academic and not context and config.STRICT_GROUNDING and not image_url:
        answer, cached, counts = STRICT_REFUSAL, False, False
    else:
        history = await run(_history, uid, conv)
        answer = await _answer(history, question_for_model, image_url, context)
        cached = False

    # --- record ---
    stored_user = text + (("  " + attachment_note) if attachment_note else "")
    user_row = await run(_save, user, conv, "user", stored_user or "(attachment)",
                         subject_id=subject_id, topic_id=tagging["topic_id"], subtopic_id=tagging["subtopic_id"],
                         counts_against_allowance=bool(counts and not cached), cached=cached,
                         channel=channel, client_ref=client_ref, intent="question" if academic else "chat")
    source = None
    if counts and not cached:
        spent = await run(allowance.consume, uid, "question", f"msg:{user_row['id']}", subject_id)
        source = spent["source"] if spent["allowed"] else "overdraw"  # never cut off mid-answer
        await run(lambda: db().table("reg_messages").update({"allowance_source": source})
                  .eq("id", user_row["id"]).execute())
    asst_row = await run(_save, user, conv, "assistant", answer,
                         subject_id=subject_id, topic_id=tagging["topic_id"], subtopic_id=tagging["subtopic_id"],
                         cached=cached, channel=channel,
                         client_ref=(client_ref + ":a") if client_ref else None,
                         metadata={"question_hash": qhash} if qhash else {})

    if academic and tagging["topic_id"] and is_new_problem:
        await run(rpc, "reg_bump_topic_stat", {"p_user": uid, "p_topic": tagging["topic_id"],
                                               "p_subject": subject_id, "p_asked": 1})
    if qhash and not cached and context and answer != STRICT_REFUSAL:
        await run(lambda: db().table("reg_answer_cache").upsert({
            "question_hash": qhash, "question": text[:2000], "answer": answer,
            "subject_id": subject_id, "topic_id": tagging["topic_id"]}, on_conflict="question_hash",
            ignore_duplicates=True).execute())

    return _reply_payload(asst_row, conversation_id=conv, counted=bool(counts and not cached),
                          source=source, follow_ups_left=_follow_ups_left(prior_turns + 1))


def _follow_ups_left(turns_including_this: int) -> int:
    used = (turns_including_this - 1) % (config.FREE_FOLLOW_UPS + 1)
    return config.FREE_FOLLOW_UPS - used


def _topic_name(topic_id: Optional[str]) -> Optional[str]:
    if not topic_id:
        return None
    row = one(db().table("reg_syllabus_topics").select("name").eq("id", topic_id))
    return row["name"] if row else None


def _reply_payload(row: dict, *, conversation_id: str, counted: bool = False, source: Optional[str] = None,
                   follow_ups_left: Optional[int] = None, replayed: bool = False) -> dict:
    return {
        "conversation_id": conversation_id,
        "message_id": row["id"],
        "answer": row["content"],
        "subject_id": row.get("subject_id"),
        "topic_id": row.get("topic_id"),
        "topic": _topic_name(row.get("topic_id")),
        "cached": bool(row.get("cached")),
        "counted": counted,
        "allowance_source": source,
        "follow_ups_left": follow_ups_left,
        "replayed": replayed,
    }


# --------------------------------------------------------------------------
# "Still unsure" and ratings (FR-S1, LLM-7)
# --------------------------------------------------------------------------
def _own_assistant_message(user_id: str, message_id: str) -> dict:
    row = one(db().table("reg_messages").select("*").eq("id", message_id).eq("user_id", user_id))
    if not row or row["role"] != "assistant":
        raise LookupError("Answer not found")
    return row


async def still_unsure(user: dict, message_id: str) -> dict:
    """Simpler re-explanation. Free, and logs a persistent weakness on the topic."""
    uid = user["id"]
    target = await run(_own_assistant_message, uid, message_id)
    conv = target["conversation_id"]
    history = await run(_history, uid, conv)
    answer = await _answer(history, "I'm still unsure. Please explain it more simply.", None, "",
                           extra_system=UNSURE_PROMPT)
    await run(lambda: db().table("reg_messages").update({"unsure": True}).eq("id", message_id).execute())
    await run(_save, user, conv, "user", "Still unsure", is_reexplanation=True,
              subject_id=target.get("subject_id"), topic_id=target.get("topic_id"), intent="unsure")
    row = await run(_save, user, conv, "assistant", answer, is_reexplanation=True,
                    subject_id=target.get("subject_id"), topic_id=target.get("topic_id"))
    if target.get("topic_id") and target.get("subject_id"):
        await run(rpc, "reg_bump_topic_stat", {"p_user": uid, "p_topic": target["topic_id"],
                                               "p_subject": target["subject_id"], "p_unsure": 1})
    return _reply_payload(row, conversation_id=conv)


async def rate(user: dict, message_id: str, value: int) -> dict:
    """Thumbs up/down. A thumbs-down sends the answer to the review queue and
    stops it being served from the cache."""
    if value not in (1, -1):
        raise ValueError("rating must be 1 or -1")
    target = await run(_own_assistant_message, user["id"], message_id)
    update = {"rating": value}
    if value == -1:
        update["review_status"] = "pending"
        qhash = (target.get("metadata") or {}).get("question_hash")
        if qhash:
            await run(lambda: db().table("reg_answer_cache").update({"disabled": True})
                      .eq("question_hash", qhash).execute())
    await run(lambda: db().table("reg_messages").update(update).eq("id", message_id).execute())
    return {"message_id": message_id, "rating": value}
