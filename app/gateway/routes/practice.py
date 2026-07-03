"""
Practice mode — Bloom-taxonomy-driven exam preparation.

A student picks a subject + topics; Gemini generates a Cameroon GCE A-Level
practice set spanning Bloom's six levels in a mix of MCQ, short-answer, and
structural formats; we persist the session, grade answers as they come in
(MCQ = deterministic, others = Gemini judge), and produce a Bloom/topic
breakdown when they finish.

Question generation goes straight to Gemini (with JSON mode), NOT through the
external RAG worker — the RAG worker is designed to *answer* curriculum
questions, not produce structured JSON quiz sets, so feeding it our
generation prompt previously returned prose and broke the parser.

All state lives in the controller's Supabase under `practice_sessions`.
"""
import asyncio
import json
import re
from typing import Any, Optional, Literal
from uuid import UUID, uuid4
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.gateway.database import supabase
from app.gateway.routes.llm_clients import async_openrouter_client, GEMINI_MODEL, image_data_url
from app.gateway.routes.text_cleanup import clean_math_notation, clean_question_strings

router = APIRouter(prefix="/api/practice", tags=["Practice"])

# ---------------------------------------------------------------------------
# Constants & prompts
# ---------------------------------------------------------------------------

BLOOM_LEVELS = ["remember", "understand", "apply", "analyze", "evaluate", "create"]
QUESTION_TYPES = ["mcq", "short_answer", "structural"]
DEFAULT_COUNT = 6
MIN_COUNT = 3
MAX_COUNT = 12
DEFAULT_CLASS_LEVEL = "a-level"
JUDGE_TIMEOUT = 25.0

# Reuse the controller's UUID regex so we accept either a UUID or a phone
# number (which we resolve via the users table).
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _resolve_user_uuid(raw_id: str) -> Optional[str]:
    if not raw_id:
        return None
    if UUID_RE.match(raw_id):
        return raw_id
    try:
        res = (
            supabase.table("users")
            .select("id")
            .eq("phone_number", raw_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]["id"]
    except Exception as e:
        print(f"User UUID lookup failed for {raw_id!r}: {e}")
    return None


GENERATION_PROMPT_TEMPLATE = """\
You are CARATI's exam-prep agent for Cameroon GCE Advanced Level (A-Level)
students. Generate a practice set for A-Level {subject} on these topics:
{topics_csv}.

CRITICAL — NO LATEX / NO MARKDOWN:
- Do NOT use LaTeX. Never wrap maths in $...$, $$...$$, \\(...\\), or \\[...\\].
- Do NOT emit backslashed commands like \\frac, \\sqrt, \\pi, \\times,
  \\alpha, \\theta. Use the Unicode characters directly: π, √, ×, α, θ.
- For powers, use Unicode superscripts: x², x³, xⁿ, 10⁻³ (NOT x^2 or x^{n}).
- For subscripts, use Unicode subscripts: H₂O, CO₂, vₓ (NOT H_2O or v_x).
- For fractions, use a slash: (x+1)/(x-1) (NOT \\frac{x+1}{x-1}).
- For square roots, use √: √2, √(x+1).
- No **bold**, *italic*, # headers, ` backticks, or `*`/`-` bullets.
- Plain prose only. Keep formulas inline as plain text.

CRITICAL — CAMEROON GCE A-LEVEL STYLE:
Every question MUST be styled like a real Cameroon GCE Advanced Level past
paper question. Assume A-Level rigour throughout — do NOT pitch questions
at O-Level depth. That means:
  - The phrasing, mark scheme conventions, command words ("Define",
    "Explain", "State and explain", "Calculate", "Show that", "Distinguish
    between", "With the aid of a diagram…"), and depth of expected answer
    match real GCE Cameroon A-Level papers.
  - Marks per structural question follow GCE conventions (typically 2–8
    marks, with 3, 4, 5, and 6 most common).
  - Topics, examples, units (SI), notation, and contexts are aligned to
    the Cameroon syllabus. Where natural, use Cameroonian / West African
    contexts (CFA francs, local commodities, local agriculture).
  - Subject scope covers any Cameroon GCE subject: mathematics, further
    mathematics, physics, chemistry, biology, computer science / ICT,
    geography, history, economics, religious studies, literature in
    english, english language, french, citizenship, philosophy, geology,
    accounting, commerce, food and nutrition, and similar.

REQUIREMENTS:
- Total questions: {count}.
- Difficulty: {difficulty}.
- ALLOWED QUESTION TYPES: {types_csv}. Use ONLY these types. {type_mix_rule}
- Distribute across Bloom's six levels — remember, understand, apply, analyze,
  evaluate, create — favouring the higher-order levels (apply/analyze/
  evaluate/create) over pure recall.
- Each question must be tied to one of the listed topics.
- Use ONLY content that is faithful to the Cameroon GCE curriculum. Do not
  invent facts, formulas, dates, or conventions that contradict it. If a
  topic is not in the GCE syllabus for that subject/level, skip it.

QUESTION SHAPES (return EXACTLY these schemas, no extras):

  {{
    "id": "q1",
    "type": "mcq",
    "topic": "<one of the listed topics>",
    "bloom_level": "remember|understand|apply|analyze|evaluate|create",
    "prompt": "<the question text — self-contained, no 'see figure'>",
    "options": ["A. ...", "B. ...", "C. ...", "D. ..."],
    "correct_answer": "A|B|C|D",
    "explanation": "<why the correct option is right; 1-3 sentences>"
  }}

  {{
    "id": "q2",
    "type": "short_answer",
    "topic": "...",
    "bloom_level": "...",
    "prompt": "...",
    "correct_answer": "<the canonical answer — a value, term, or one-line phrase>",
    "acceptable_variations": ["<synonym or alternate notation>", "..."],
    "explanation": "..."
  }}

  {{
    "id": "q3",
    "type": "structural",
    "topic": "...",
    "bloom_level": "...",
    "prompt": "<may contain (a), (b), (c) sub-parts inline>",
    "model_answer": "<a complete worked answer covering every sub-part>",
    "marking_points": ["<key idea 1>", "<key idea 2>", "..."],
    "max_marks": <int between 3 and 8>,
    "explanation": "<1-2 sentence summary of the concept being tested>"
  }}

OUTPUT — JSON ONLY, no prose, no markdown fence:
{{
  "questions": [ ...exactly {count} items... ]
}}

The id values must be q1, q2, q3, ... in order.
"""


JUDGE_PROMPT_TEMPLATE = """\
You are grading a {class_level} {subject} student's answer to a {qtype}
question on "{topic}" (Bloom level: {bloom}).

QUESTION:
{prompt}

MODEL ANSWER / EXPECTED:
{expected}

{marking_block}

STUDENT'S ANSWER (may be typed text below, or a photo of handwritten work
provided alongside this prompt):
{student_answer}

NO LATEX / NO MARKDOWN IN YOUR FEEDBACK:
- No $...$, $$...$$, \\(, \\[. No \\frac, \\sqrt, \\pi, \\times.
- Use Unicode super/subscripts: x², H₂O, 10⁻³.
- Use π, √, ×, ±, α, θ directly. Fractions as a/b. Plain prose only.

GRADING RULES:
- Read the student's working step by step.
- For STRUCTURAL questions, identify EXACTLY where in their working they went
  wrong, marking-point by marking-point. Tell them which line/step is the
  first error, what they wrote, and what they should have written. Do not
  just say "incorrect" — be specific.
- For SHORT_ANSWER, accept synonyms / equivalent notations / unit-equivalent
  values. Be charitable on formatting; strict on facts.
- For partial credit on multi-mark questions, assign credit per marking point
  achieved.

Return ONLY this JSON (no prose, no fence):
{{
  "score": <number between 0 and 1, with 1 = fully correct>,
  "is_correct": <true if score >= 0.7, else false>,
  "feedback": "<2-3 sentences: what they got right, what to improve, and a
                concrete next step. Be warm and direct, not condescending.>",
  "step_analysis": [
    {{
      "step": "<the marking point or working step being judged>",
      "got_right": <true|false>,
      "where_wrong": "<ONLY when got_right=false: pinpoint the exact line/
                      working/value that's incorrect, what was written, and
                      what should have been written. Empty string when got_right=true.>"
    }}
  ]
}}

For mcq and short_answer where there's only one fact to check, step_analysis
may be a single-item array.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_correct(questions: list[dict]) -> list[dict]:
    """Remove answer keys before sending questions to the student."""
    safe = []
    for q in questions:
        clean = {k: v for k, v in q.items() if k not in (
            "correct_answer", "acceptable_variations", "model_answer",
            "marking_points", "explanation",
        )}
        safe.append(clean)
    return safe


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _topics_csv(topics: list[str]) -> str:
    return ", ".join(t.strip() for t in topics if t and t.strip()) or "(any topic)"


async def _generate_questions(
    user_id: str, subject: str, topics: list[str], class_level: str,
    count: int, difficulty: str, types: list[str],
) -> list[dict]:
    """Generate a Bloom-distributed question set directly via Gemini.

    Question generation is a structured-output task, not a retrieval task —
    we call the LLM in JSON mode so the parser always sees valid JSON.
    Works for any Cameroon GCE A-Level subject the student picks.
    """
    types_csv = ", ".join(types)
    if len(types) == 1:
        type_mix_rule = (
            f"Every question MUST be of type '{types[0]}'. Do not produce any "
            f"other type."
        )
    else:
        type_mix_rule = (
            "Spread the questions across these types so the student gets "
            "meaningful practice in each — include at least one of every "
            "allowed type."
        )
    prompt = GENERATION_PROMPT_TEMPLATE.format(
        class_level=class_level,
        subject=subject,
        topics_csv=_topics_csv(topics),
        count=count,
        difficulty=difficulty,
        types_csv=types_csv,
        type_mix_rule=type_mix_rule,
    )

    try:
        resp = await asyncio.wait_for(
            async_openrouter_client.chat.completions.create(
                model=GEMINI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.55,
                response_format={"type": "json_object"},
            ),
            timeout=90.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Question generation timed out — please try again.")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Question generation failed: {str(e)[:200]}")

    raw = resp.choices[0].message.content if resp.choices else "{}"
    questions = _parse_questions_payload(raw or "{}", count)
    # Defensive client-side filter: drop anything outside the requested types
    # in case the model slipped one in.
    allowed = set(types)
    filtered = [q for q in questions if q.get("type") in allowed] or questions
    # Convert any LaTeX-flavoured math the model emitted ($x^3$, H_2O, \pi…)
    # into plain Unicode so the chat UI renders it cleanly.
    return [clean_question_strings(q) for q in filtered]


def _parse_questions_payload(raw: str, expected_count: int) -> list[dict]:
    """Tolerant JSON extractor — handles prose-wrapped or fenced JSON, just
    in case the model strays from the requested JSON shape."""
    text = (raw or "").strip()
    # Strip ```json ... ``` fences if present.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Find the first '{' to the last '}' as a fallback.
    if not text.startswith("{"):
        l = text.find("{")
        r = text.rfind("}")
        if l != -1 and r != -1 and r > l:
            text = text[l:r + 1]
    try:
        parsed = json.loads(text)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Question generator returned non-JSON: {str(e)[:200]}",
        )
    questions = parsed.get("questions") if isinstance(parsed, dict) else None
    if not isinstance(questions, list) or not questions:
        raise HTTPException(
            status_code=502,
            detail="Question generator returned an empty or malformed practice set.",
        )
    # Normalise + validate each entry — drop anything that doesn't fit.
    cleaned = []
    for idx, q in enumerate(questions):
        if not isinstance(q, dict):
            continue
        qtype = q.get("type")
        if qtype not in QUESTION_TYPES:
            continue
        q.setdefault("id", f"q{idx + 1}")
        q.setdefault("bloom_level", "understand")
        q.setdefault("topic", "")
        if qtype == "mcq":
            opts = q.get("options")
            if not isinstance(opts, list) or len(opts) < 2:
                continue
            ca = (q.get("correct_answer") or "").strip().upper()[:1]
            if ca not in {"A", "B", "C", "D", "E"}:
                continue
            q["correct_answer"] = ca
        elif qtype == "short_answer":
            if not q.get("correct_answer"):
                continue
            q.setdefault("acceptable_variations", [])
        elif qtype == "structural":
            if not q.get("model_answer"):
                continue
            q.setdefault("marking_points", [])
            try:
                q["max_marks"] = int(q.get("max_marks") or 5)
            except Exception:
                q["max_marks"] = 5
        cleaned.append(q)
    if len(cleaned) < min(MIN_COUNT, expected_count):
        raise HTTPException(
            status_code=502,
            detail=f"RAG produced too few valid questions ({len(cleaned)}).",
        )
    return cleaned


def _normalise_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _grade_mcq(question: dict, student_answer: str) -> dict:
    correct = (question.get("correct_answer") or "").strip().upper()[:1]
    chosen = (student_answer or "").strip().upper()[:1]
    is_correct = bool(chosen) and chosen == correct
    return {
        "score": 1.0 if is_correct else 0.0,
        "is_correct": is_correct,
        "feedback": (
            f"Correct — {question.get('explanation', '')}" if is_correct
            else f"The right answer is {correct}. {question.get('explanation', '')}"
        ).strip(),
    }


def _grade_short_answer_fast_path(question: dict, student_answer: str) -> Optional[dict]:
    """Try a deterministic match against canonical + acceptable variations.
    Returns None when none match — caller should fall back to the LLM judge.
    """
    norm_student = _normalise_text(student_answer)
    if not norm_student:
        return {"score": 0.0, "is_correct": False, "feedback": "No answer provided."}
    candidates = [question.get("correct_answer", "")]
    candidates.extend(question.get("acceptable_variations") or [])
    for cand in candidates:
        if not cand:
            continue
        if _normalise_text(cand) == norm_student:
            return {
                "score": 1.0,
                "is_correct": True,
                "feedback": f"Correct. {question.get('explanation', '')}".strip(),
            }
    return None


async def _grade_with_judge(
    question: dict,
    student_answer: str,
    subject: str,
    class_level: str,
    image_data: Optional[str] = None,
) -> dict:
    if question["type"] == "structural":
        expected = question.get("model_answer", "")
        marking_points = question.get("marking_points") or []
        marking_block = (
            "MARKING POINTS (each ≈ equal weight):\n- " + "\n- ".join(marking_points)
            if marking_points else "MARKING POINTS: (none specified)"
        )
    else:
        expected = question.get("correct_answer", "")
        variations = question.get("acceptable_variations") or []
        marking_block = (
            "ACCEPTABLE VARIATIONS: " + ", ".join(variations)
            if variations else "ACCEPTABLE VARIATIONS: (none)"
        )
    prompt = JUDGE_PROMPT_TEMPLATE.format(
        class_level=class_level,
        subject=subject,
        qtype=question["type"],
        topic=question.get("topic", ""),
        bloom=question.get("bloom_level", ""),
        prompt=question.get("prompt", ""),
        expected=expected,
        marking_block=marking_block,
        student_answer=student_answer or "(typed text not provided — see attached photo of handwritten work)",
    )

    if image_data:
        user_content: Any = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_data}},
        ]
    else:
        user_content = prompt

    try:
        resp = await asyncio.wait_for(
            async_openrouter_client.chat.completions.create(
                model=GEMINI_MODEL,
                messages=[{"role": "user", "content": user_content}],
                temperature=0.1,
                response_format={"type": "json_object"},
            ),
            timeout=JUDGE_TIMEOUT,
        )
    except Exception as e:
        print(f"Judge call failed: {e}")
        return {
            "score": 0.0,
            "is_correct": False,
            "feedback": "I couldn't grade this answer right now — please try again in a moment.",
            "step_analysis": [],
        }
    raw = resp.choices[0].message.content if resp.choices else "{}"
    try:
        data = json.loads(raw or "{}")
    except Exception:
        data = {}
    score = float(data.get("score") or 0.0)
    score = max(0.0, min(1.0, score))
    raw_steps = data.get("step_analysis") or []
    step_analysis = []
    if isinstance(raw_steps, list):
        for s in raw_steps:
            if not isinstance(s, dict):
                continue
            step_analysis.append({
                "step": clean_math_notation((s.get("step") or "").strip()),
                "got_right": bool(s.get("got_right")),
                "where_wrong": clean_math_notation((s.get("where_wrong") or "").strip()),
            })
    return {
        "score": score,
        "is_correct": bool(data.get("is_correct", score >= 0.7)),
        "feedback": clean_math_notation(
            (data.get("feedback") or "").strip()
            or "Graded — see model answer for details."
        ),
        "step_analysis": step_analysis,
    }


def _summarise(session: dict) -> dict:
    """Compute Bloom/topic/type breakdowns for a finished session."""
    questions = session.get("questions") or []
    answers = session.get("answers") or {}
    by_q = {q["id"]: q for q in questions if "id" in q}

    total_score = 0.0
    total_weight = 0.0
    by_bloom: dict[str, dict] = {}
    by_topic: dict[str, dict] = {}
    by_type: dict[str, dict] = {}
    weak_topics: list[str] = []

    def _bucket(d: dict, key: str) -> dict:
        if key not in d:
            d[key] = {"score_sum": 0.0, "weight_sum": 0.0, "count": 0}
        return d[key]

    for qid, ans in answers.items():
        q = by_q.get(qid)
        if not q:
            continue
        weight = (q.get("max_marks") or 1) if q.get("type") == "structural" else 1
        s = float(ans.get("score") or 0.0) * weight
        total_score += s
        total_weight += weight
        for d, key in (
            (by_bloom, q.get("bloom_level") or "unknown"),
            (by_topic, q.get("topic") or "unknown"),
            (by_type, q.get("type") or "unknown"),
        ):
            b = _bucket(d, key)
            b["score_sum"] += s
            b["weight_sum"] += weight
            b["count"] += 1

    def _finalize(d: dict) -> dict:
        out = {}
        for key, b in d.items():
            pct = (b["score_sum"] / b["weight_sum"] * 100) if b["weight_sum"] else 0.0
            out[key] = {"score_pct": round(pct, 1), "count": b["count"]}
        return out

    final_pct = round((total_score / total_weight * 100) if total_weight else 0.0, 1)
    finalized_topics = _finalize(by_topic)
    weak_topics = [t for t, v in finalized_topics.items() if v["score_pct"] < 60]

    return {
        "score_pct": final_pct,
        "by_bloom": _finalize(by_bloom),
        "by_topic": finalized_topics,
        "by_type": _finalize(by_type),
        "weak_topics": weak_topics,
    }


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class StartRequest(BaseModel):
    user_id: str
    subject: str
    topics: list[str] = Field(default_factory=list)
    class_level: str = DEFAULT_CLASS_LEVEL
    count: int = DEFAULT_COUNT
    difficulty: Literal["easy", "medium", "hard", "mixed"] = "mixed"
    # Empty / omitted means "all three" — i.e. mixed. The frontend may pass
    # any subset of {"mcq", "short_answer", "structural"}.
    types: list[Literal["mcq", "short_answer", "structural"]] = Field(default_factory=list)


class FinishRequest(BaseModel):
    session_id: UUID


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/start")
async def start_practice(req: StartRequest):
    user_uuid = _resolve_user_uuid(req.user_id)
    if not user_uuid:
        raise HTTPException(status_code=404, detail="User not found.")

    if not req.topics:
        raise HTTPException(status_code=400, detail="At least one topic is required.")
    count = max(MIN_COUNT, min(MAX_COUNT, req.count))
    types = req.types or list(QUESTION_TYPES)
    # Validate; drop unknowns silently rather than 422.
    types = [t for t in types if t in QUESTION_TYPES] or list(QUESTION_TYPES)

    questions = await _generate_questions(
        req.user_id, req.subject, req.topics, req.class_level,
        count, req.difficulty, types,
    )

    row = {
        "userid": user_uuid,
        "subject": req.subject,
        "topics": req.topics,
        "class_level": req.class_level,
        "difficulty": req.difficulty,
        "questions": questions,
        "answers": {},
        "status": "in_progress",
        "started_at": _now_iso(),
    }
    # Note: types selected for this session are encoded in the questions
    # produced; we don't separately persist them since the row's questions
    # already reflect the filter.
    try:
        res = supabase.table("practice_sessions").insert(row).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to persist session: {e}")

    if not res.data:
        raise HTTPException(status_code=500, detail="Session insert returned no row.")
    session = res.data[0]

    return {
        "session_id": session["id"],
        "subject": req.subject,
        "topics": req.topics,
        "class_level": req.class_level,
        "difficulty": req.difficulty,
        "questions": _strip_correct(questions),
    }


@router.post("/answer")
async def answer_question(
    session_id: UUID = Form(...),
    question_id: str = Form(...),
    answer: str = Form(""),
    file: UploadFile = File(None),  # optional image of handwritten work
):
    sid = str(session_id)
    res = (
        supabase.table("practice_sessions")
        .select("*").eq("id", sid).limit(1).execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Session not found.")
    session = res.data[0]
    if session["status"] != "in_progress":
        raise HTTPException(status_code=409, detail=f"Session is already {session['status']}.")

    question = next((q for q in (session.get("questions") or []) if q.get("id") == question_id), None)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found in this session.")

    qtype = question.get("type")
    if qtype not in QUESTION_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown question type {qtype!r}.")

    # Reject empty submissions outright.
    has_text = bool((answer or "").strip())
    image_data = None
    if file is not None and (file.content_type or "").startswith("image"):
        try:
            file_bytes = await file.read()
            if file_bytes:
                image_data = image_data_url(file_bytes, file.content_type or "image/jpeg")
        except Exception as e:
            print(f"Reading uploaded answer image failed: {e}")
            image_data = None
    if not has_text and not image_data:
        raise HTTPException(status_code=400, detail="Provide an answer (text or image).")

    # MCQ never uses an image — it's always one of A/B/C/D.
    if qtype == "mcq":
        grade = _grade_mcq(question, answer)
    elif qtype == "short_answer":
        # If text only, try the fast path before paying for an LLM call.
        if has_text and not image_data:
            grade = _grade_short_answer_fast_path(question, answer)
            if grade is None:
                grade = await _grade_with_judge(
                    question, answer, session["subject"], session["class_level"],
                )
        else:
            grade = await _grade_with_judge(
                question, answer, session["subject"], session["class_level"],
                image_data=image_data,
            )
    else:  # structural
        grade = await _grade_with_judge(
            question, answer, session["subject"], session["class_level"],
            image_data=image_data,
        )
    # MCQ doesn't get LLM step_analysis — synthesize a single-step entry so
    # the client renders the same shape for every question.
    if qtype == "mcq" and "step_analysis" not in grade:
        grade["step_analysis"] = [{
            "step": question.get("prompt", "")[:80] or "MCQ",
            "got_right": bool(grade.get("is_correct")),
            "where_wrong": "" if grade.get("is_correct") else
                f"You picked {(answer or '').strip().upper()[:1] or '—'}; "
                f"correct is {question.get('correct_answer','')}.",
        }]

    answers = dict(session.get("answers") or {})
    answers[question_id] = {
        "answer": answer,
        # Flag — not the image itself. We don't persist the image bytes.
        "answer_has_image": bool(image_data),
        **grade,
        "answered_at": _now_iso(),
    }

    try:
        supabase.table("practice_sessions").update({"answers": answers}).eq("id", sid).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save answer: {e}")

    answered = len(answers)
    total = len(session.get("questions") or [])
    return {
        "is_correct": grade["is_correct"],
        "score": grade["score"],
        "feedback": grade["feedback"],
        "step_analysis": grade.get("step_analysis") or [],
        "model_answer": question.get("model_answer") or question.get("correct_answer"),
        "explanation": question.get("explanation"),
        "progress": {"answered": answered, "total": total},
    }


@router.post("/finish")
async def finish_practice(req: FinishRequest):
    sid = str(req.session_id)
    res = (
        supabase.table("practice_sessions")
        .select("*").eq("id", sid).limit(1).execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Session not found.")
    session = res.data[0]

    summary = _summarise(session)
    update = {
        "status": "completed",
        "score": summary["score_pct"],
        "completed_at": _now_iso(),
    }
    try:
        supabase.table("practice_sessions").update(update).eq("id", sid).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to finish session: {e}")

    return {
        "session_id": sid,
        "subject": session["subject"],
        "topics": session.get("topics") or [],
        "summary": summary,
        "completed_at": update["completed_at"],
    }


@router.get("/sessions")
async def list_sessions(user_id: str, limit: int = 20):
    user_uuid = _resolve_user_uuid(user_id)
    if not user_uuid:
        raise HTTPException(status_code=404, detail="User not found.")

    limit = max(1, min(100, limit))
    res = (
        supabase.table("practice_sessions")
        .select("id, subject, topics, class_level, difficulty, score, status, "
                "started_at, completed_at, questions, answers")
        .eq("userid", user_uuid)
        .order("started_at", desc=True)
        .limit(limit)
        .execute()
    )
    items = []
    for s in res.data or []:
        total = len(s.get("questions") or [])
        answered = len(s.get("answers") or {})
        items.append({
            "id": s["id"],
            "subject": s["subject"],
            "topics": s.get("topics") or [],
            "class_level": s.get("class_level"),
            "difficulty": s.get("difficulty"),
            "score": s.get("score"),
            "status": s["status"],
            "started_at": s.get("started_at"),
            "completed_at": s.get("completed_at"),
            "progress": {"answered": answered, "total": total},
        })
    return {"sessions": items}


@router.get("/sessions/{session_id}")
async def get_session(session_id: UUID):
    res = (
        supabase.table("practice_sessions")
        .select("*").eq("id", str(session_id)).limit(1).execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Session not found.")
    session = res.data[0]
    summary = _summarise(session) if session["status"] == "completed" else None
    return {
        "id": session["id"],
        "subject": session["subject"],
        "topics": session.get("topics") or [],
        "class_level": session.get("class_level"),
        "difficulty": session.get("difficulty"),
        "status": session["status"],
        "score": session.get("score"),
        "started_at": session.get("started_at"),
        "completed_at": session.get("completed_at"),
        # Reveal correct answers + explanations only after the session is done.
        "questions": (session.get("questions") or []) if session["status"] == "completed"
                     else _strip_correct(session.get("questions") or []),
        "answers": session.get("answers") or {},
        "summary": summary,
    }
