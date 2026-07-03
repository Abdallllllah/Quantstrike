
import os
import re
import json
import asyncio
from datetime import datetime, timezone
import httpx
import redis.asyncio as redis
from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from app.gateway.database import supabase
from app.gateway.routes.llm_clients import (
    async_openrouter_client,
    async_groq_client,
    image_data_url,
    GEMINI_MODEL,
    WHISPER_MODEL,
)
from app.gateway.routes.text_cleanup import clean_math_notation

load_dotenv()

router = APIRouter(prefix="/api/chat", tags=["Master Controller"])

# Frontend may pass either a UUID or the user's phone number as `user_id`.
# Persist endpoint requires a UUID, so we normalise here once per request.
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _resolve_user_uuid(raw_id: str) -> str | None:
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

# Configuration
BASE_URL = os.getenv("BASE_URL") or f"http://127.0.0.1:{os.getenv('PORT', '8000')}"
RAG_URL = f"{BASE_URL}/api/rag"
EXTRACT_URL = f"{BASE_URL}/api/extract"
MESSAGES_URL = f"{BASE_URL}/api/messages/insert"
HARDCODED_CLASS = "a-level"
HARDCODED_SCHOOL_ID = "5526b246-d220-4198-95ef-29843fccfd3f"
AI_TUTOR_USER_ID = "e8a5cb30-ed1d-4a7b-8b4e-c8fd06536392"
AI_TUTOR_ROLE = "tutor"

# Timeouts (seconds). DOWNSTREAM_TIMEOUT must exceed rag_router's read timeout (90s)
# so this caller does not give up before /api/rag itself does. ROUTER_TIMEOUT
# is generous because OpenRouter free-tier Gemini router can have cold-start latency.
ROUTER_TIMEOUT = 45.0
TRANSCRIBE_TIMEOUT = 30.0
DOWNSTREAM_TIMEOUT = 110.0
PERSIST_TIMEOUT = 5.0
REDIS_TIMEOUT = 3.0

FALLBACK_ANSWER = "I'm having trouble analyzing that right now. Please try again."

# Short replies that look like paper-extraction follow-ups (year, paper number,
# yes/no). Used to decide whether to keep the student in the multi-turn extract
# flow or let them switch to a different question.
_PAPER_DETAIL_PATTERNS = [
    re.compile(r"^(19|20)\d{2}$"),                             # "2020"
    re.compile(r"^paper\s*[1-5]$", re.IGNORECASE),             # "Paper 2"
    re.compile(r"^p\s*[1-5]$", re.IGNORECASE),                 # "P2"
    re.compile(r"^[1-5]$"),                                    # "2"
    re.compile(r"^paper\s+(one|two|three|four|five)$", re.IGNORECASE),
    re.compile(r"^(one|two|three|four|five)$", re.IGNORECASE),
    re.compile(r"^(a|o)[\s-]*level$", re.IGNORECASE),          # "A-Level", "O level"
]
_PAPER_AFFIRMATIONS = {"yes", "yeah", "yep", "yup", "ok", "okay", "sure", "please"}


def _looks_like_paper_detail(msg: str) -> bool:
    text = (msg or "").strip()
    if not text or len(text) > 30:
        return False
    if text.lower() in _PAPER_AFFIRMATIONS:
        return True
    return any(p.match(text) for p in _PAPER_DETAIL_PATTERNS)

# Daily student-message caps per tier. Counts only role='student' rows in the
# `messages` table whose timestamp falls in the current UTC day.
TIER_LIMITS = {1: 25, 2: 60, 3: 100}
DEFAULT_TIER = 1


def _utc_day_start_iso() -> str:
    # Use a date-only string (YYYY-MM-DD) for the gte filter. Postgres will
    # interpret it as midnight at the column's tz. This avoids URL-encoding
    # quirks with the `+00:00` suffix that previously caused the count to
    # silently return 0 even when the user had sent messages today.
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _get_user_tier(user_uuid: str) -> int:
    try:
        res = (
            supabase.table("users")
            .select("tier")
            .eq("id", user_uuid)
            .limit(1)
            .execute()
        )
        if res.data:
            return int(res.data[0].get("tier") or DEFAULT_TIER)
    except Exception as e:
        print(f"Tier lookup failed for {user_uuid}: {e}")
    return DEFAULT_TIER


def _count_today_student_messages(user_uuid: str) -> int:
    try:
        # Note: the messages table's primary key is `messageid`, not `id`.
        # PostgREST errors out when you select a non-existent column, which
        # was silently returning 0 here regardless of how many rows existed.
        res = (
            supabase.table("messages")
            .select("messageid", count="exact")
            .eq("userid", user_uuid)
            .eq("role", "student")
            .gte("timestamp", _utc_day_start_iso())
            .execute()
        )
        return int(res.count or 0)
    except Exception as e:
        print(f"Daily message count failed for {user_uuid}: {e}")
        return 0


def _usage_snapshot(user_uuid: str) -> dict:
    tier = _get_user_tier(user_uuid)
    limit = TIER_LIMITS.get(tier, TIER_LIMITS[DEFAULT_TIER])
    used = _count_today_student_messages(user_uuid)
    return {
        "tier": tier,
        "limit": limit,
        "used": used,
        "remaining": max(0, limit - used),
    }

redis_client = redis.from_url(
    os.getenv("REDIS_URL"),
    decode_responses=True,
    socket_timeout=REDIS_TIMEOUT,
    socket_connect_timeout=REDIS_TIMEOUT,
)

ROUTER_PROMPT = """
You are CARATI, an AI study companion built to help students learn.
You have been trained on the Cameroon GCE Advanced Level (A-Level)
curriculum and you align every academic explanation, example, notation,
unit, and exam style to the Cameroon GCE A-Level syllabus.

IDENTITY RULES (must always hold):
- Your name is exactly "CARATI". Never call yourself "Carati AI", "an AI tutor", "a tutor", or "an assistant tutor".
- You are not a tutor. You are CARATI, here to assist students with their studies.
- When asked who or what you are, say: "I am CARATI, here to help you study."
- Speak warmly and directly to the student. Confident, not boastful.

CURRICULUM CONTEXT:
- The student is preparing for the Cameroon GCE Advanced Level (A-Level).
  Every academic answer MUST be aligned to that syllabus — its topic scope,
  vocabulary, notation, expected depth, and exam-question style. Do not
  pitch answers at O-Level depth — assume A-Level rigour at all times.
- When citing examples, prefer Cameroonian / West African contexts where
  natural (CFA francs, local commodities, common GCE A-Level worked examples).
- Use SI units, GCE-standard symbols, and the level of explanation suitable
  for an Advanced Level student.

SUBJECT SCOPE: every subject taught in the Cameroon GCE A-Level curriculum,
including but not limited to: mathematics, further mathematics, physics,
chemistry, biology, computer science, geography, history, economics,
religious studies, literature in english, english language, french,
citizenship, philosophy, geology, accounting, commerce. Use the subject the
student is studying.

INPUT-MODE RULES:
- Text alone is allowed.
- Image alone is allowed. Image PLUS text is allowed.
- Audio alone is allowed. Audio MUST NOT be combined with text — if it is, treat the request as invalid.

HISTORY USAGE — CRITICAL FOR ACADEMIC FOLLOW-UPS:
The [HISTORY] block is the recent conversation, oldest first. Treat it as the
ground truth for what the student is currently studying. Use it to:

1. RESOLVE referents ("it", "this", "that", "those", "they", "the formula",
   "the equation", "the same one") to whatever they refer to in history.
2. INHERIT the active topic and subject when the new message is a short
   follow-up. A follow-up is any message that is shorter than ~12 words and
   does not introduce a new subject by itself.
3. CARRY THE TOPIC INTO contextualized_query VERBATIM. The contextualized_query
   is sent to the curriculum search; if the topic words are missing the search
   will return nothing. Always prepend the active topic to the rewritten query
   when it is not already there.
4. INHERIT subject the same way. If recent turns were physics and the user now
   asks an ambiguous follow-up, subject MUST stay "physics" — do NOT downgrade
   to "general" just because the follow-up sentence is bare.
5. DETECT genuine topic switches. If the new message clearly opens a different
   area ("now lets do calculus", "moving on to acids and bases", "different
   question:") drop the old topic and use the new one.

WORKED EXAMPLE:
  History last turn was about thermodynamics in physics.
  User now asks: "what are the types of energy?"
  -> intent: "academic"
  -> subject: "physics"           (inherited, NOT "general")
  -> contextualized_query:
     "In thermodynamics, what are the types of energy?"

TASKS:
1. AUDIO: If audio is provided, transcribe it perfectly into the contextualized_query,
   then apply HISTORY USAGE rules to that transcript.

2. IMAGE — CRITICAL RULES (the curriculum search has NO access to the image):
   The downstream curriculum search only receives plain text. Anything visible
   in the image that is not folded into contextualized_query is permanently
   lost. So you MUST:
   a) Transcribe any printed text and questions verbatim.
   b) For every diagram, figure, graph, schematic, table, free-body diagram,
      circuit, structural formula, or geometric drawing — produce a COMPLETE
      textual description that captures the visual data exhaustively:
        - every label, variable name, value, and unit
        - every arrow's direction (up, down, left, right, angle from horizontal)
        - every magnitude shown next to an arrow or symbol
        - every geometric relationship (lengths, angles, parallel/perpendicular)
        - every connection in a circuit or structure
        - axis labels, scale, units, and any plotted values for graphs
        - row/column data for tables
      The description must be SELF-CONTAINED: a person who cannot see the image
      must be able to answer the question using only your description.
   c) NEVER write phrases like "see Figure 3", "as shown in the diagram",
      "the figure depicts", "refer to the image", or "according to the
      illustration". Those phrases assume the reader can see something they
      cannot. Replace them with the actual data.
   d) Combine the question text AND the full figure description into a single
      coherent contextualized_query.

   WORKED EXAMPLE — Free-body diagram of a bucket:
     Image shows a bucket with two arrows: F1 = 30 N pointing straight up,
     F2 = 20 N pointing straight down. The acceleration is labelled a = 2 m/s²
     upward. The question reads: "Find the mass of the bucket."
     -> contextualized_query: "A bucket has two vertical forces acting on it:
        F1 = 30 N directed upward, and F2 = 20 N directed downward. The bucket
        accelerates upward at a = 2 m/s². Find the mass of the bucket."
     (NEVER: "From Figure 3, find the mass of the bucket given forces F1 and F2.")

3. INTENT: Classify the user's need into exactly one of three values:

   - "academic": a school / curriculum question — anything in mathematics,
        physics, chemistry, or biology that can be answered from the
        curriculum. This is the default for any school question.

   - "document": the user wants a SPECIFIC PAST EXAM / QUESTION PAPER (to read,
        study, or practice with), OR has uploaded a document to extract from.
        Trigger words / phrases include any of:
          "past paper", "past papers", "question paper", "exam paper",
          "the paper", "the exam", "GCE paper", "A-level paper",
          a SPECIFIC year + subject combo (e.g. "2020 maths", "2018 physics"),
          a paper number (e.g. "paper 1", "paper 2", "paper 3"),
          requests like "send me…", "show me…", "I want…", "do you have…",
          "give me…", "where can I find…" combined with paper / exam words.
        Examples that MUST be "document":
          - "I want the 2020 maths past paper"
          - "send me physics paper 2 from 2018"
          - "do you have GCE chemistry papers?"
          - "give me a question paper to practice"
          - "where can I find the 2022 biology exam"
          - "I need the A-level maths paper 1"
          - "share the 2019 paper"
          - "any past papers for biology?"

   - "off_topic": ANYTHING that is not a school question or a paper request.
        This includes greetings, identity questions, small talk, jokes,
        weather, sports, relationships, gossip, current events, money,
        food, anything personal. ALWAYS respond by redirecting the student
        warmly back to school work. The "reply" must be short, warm, and
        always end by asking what subject they want to study.

        Examples that MUST be "off_topic":
          - "hi", "hello", "hey", "how are you?"
          - "who are you?", "what is Carati?", "are you AI?"
          - "tell me a joke", "what's the weather?", "who won the match?"
          - "do you have a girlfriend?", "are you human?"
          - "I'm bored", "what's up?"

        Reply tone examples (vary based on the actual message):
          - Greeting → "Hey! I'm Carati, ready to study. What subject can we tackle today?"
          - Identity → "I am CARATI, here to help you study. What subject would you like to start with?"
          - Other off-topic → "Let's keep our focus on school work — what subject can I help you with?"

DECISION RULES (apply in order):
  a. If the message mentions any paper / exam / past paper trigger → "document".
  b. Else, if the message is a curriculum / school question → "academic".
     If the recent history is academic and the new message is a plausible
     follow-up, PREFER "academic".
  c. Else → "off_topic". When in doubt between off_topic and academic,
     prefer academic only if the message is unambiguously about a school
     subject. Otherwise treat as off_topic.

4. ANSWERING — WHO PRODUCES THE ANSWER:
   - intent="academic"  → leave "reply" null. The downstream curriculum-
                           grounded engine (RAG) produces the answer.
   - intent="document"  → leave "reply" null. The paper-retrieval service
                           produces the response.
   - intent="off_topic" → YOU produce the reply. It must be a warm,
                           short school-redirect that ends by asking what
                           subject the student wants to study. Plain text,
                           no markdown, no "Sure!" / "Of course!" preamble.

RESPONSE FORMAT (JSON ONLY):
{
  "intent": "academic" | "document" | "off_topic",
  "subject": "mathematics" | "physics" | "chemistry" | "biology" |
             "further_mathematics" | "computer_science" | "geography" |
             "history" | "economics" | "religious_studies" |
             "literature" | "english" | "french" | "citizenship" |
             "philosophy" | "geology" | "accounting" | "commerce" | "general",
  "contextualized_query": "The rewritten standalone message — must include the
                           inherited topic words when the message is a follow-up.",
  "reply": "REQUIRED only when intent is 'off_topic' (a warm school-redirect
            ending with 'what subject would you like to study?').
            Leave null for 'academic' and 'document' — those are answered by
            downstream services."
}
"""

# Best-effort backstop for boilerplate the LLM keeps adding despite being
# told not to. Runs *after* the model returns so we don't ship "According to
# the GCE A-Level syllabus…" to the student.
_PREAMBLE_PATTERNS = [
    # Any opener that mentions GCE and ends at the next punctuation —
    # "According to the GCE A-Level chemistry syllabus,", "Per the Cameroon
    # GCE,", "Based on the GCE curriculum,", "In the Cameroon GCE A-Level
    # syllabus,", etc.
    re.compile(r"^according\s+to\s+[^,.!?\n]*?\bgce\b[^,.!?\n]*?[,.:;-]\s*", re.IGNORECASE),
    re.compile(r"^as\s+per\s+[^,.!?\n]*?\bgce\b[^,.!?\n]*?[,.:;-]\s*", re.IGNORECASE),
    re.compile(r"^per\s+[^,.!?\n]*?\bgce\b[^,.!?\n]*?[,.:;-]\s*", re.IGNORECASE),
    re.compile(r"^based\s+on\s+[^,.!?\n]*?\bgce\b[^,.!?\n]*?[,.:;-]\s*", re.IGNORECASE),
    re.compile(r"^in\s+(?:the\s+|terms\s+of\s+)?(?:cameroon\s+)?gce[^,.!?\n]*?[,.:;-]\s*", re.IGNORECASE),
    re.compile(r"^for\s+(?:the\s+|a\s+)?(?:cameroon\s+)?gce[^,.!?\n]*?[,.:;-]\s*", re.IGNORECASE),
    re.compile(r"^within\s+(?:the\s+)?(?:cameroon\s+)?gce[^,.!?\n]*?[,.:;-]\s*", re.IGNORECASE),
    # Generic openers
    re.compile(r"^(?:sure|of course|certainly|absolutely|alright|okay)[!,.\s-]+", re.IGNORECASE),
    re.compile(r"^great\s+question[!,.\s-]+", re.IGNORECASE),
    re.compile(r"^happy\s+to\s+help[!,.\s-]+", re.IGNORECASE),
    re.compile(
        r"^here['’]?s?\s+(?:your|the|an?|my)\s+(?:answer|explanation|response|reply)[\s,.:!-]+",
        re.IGNORECASE,
    ),
    re.compile(
        r"^let['’]?s\s+(?:explore|dive|begin|start|see|look)[^,.\n]*?[\s,.:!]+",
        re.IGNORECASE,
    ),
    re.compile(
        r"^this\s+is\s+(?:an?\s+)?(?:paper\s+\d+\s+)?(?:short[\s-]?answer|structured|essay)\s+style\s+question[\s,.:]*",
        re.IGNORECASE,
    ),
]

_MARKDOWN_BOLD = re.compile(r"\*\*([^*\n]+?)\*\*")
_MARKDOWN_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_MARKDOWN_HEADER = re.compile(r"^#{1,6}\s+", re.MULTILINE)


# Phrases that mean "the RAG knowledge base doesn't have this topic" — we use
# these to detect when to silently fall through to the controller LLM so the
# student never sees a dead-end "not covered" answer.
_RAG_NO_ANSWER_PATTERNS = [
    re.compile(r"\bnot\s+covered\b", re.IGNORECASE),
    re.compile(r"\bnot\s+in\s+(?:the\s+|my\s+|our\s+)?(?:curriculum|syllabus|knowledge\s+base|training|materials|database)\b", re.IGNORECASE),
    re.compile(r"\boutside\s+(?:the\s+|my\s+|of\s+(?:the\s+|my\s+)?)?(?:scope|curriculum|knowledge|training|materials)\b", re.IGNORECASE),
    re.compile(r"\bI\s+(?:don'?t|do\s+not|can'?t|cannot)\s+(?:have|provide|offer|find|answer|help)\b", re.IGNORECASE),
    re.compile(r"\bno\s+(?:information|relevant\s+\w+|answer|content|material|context|data|results?|matches?)\s+(?:found|available)?\b", re.IGNORECASE),
    re.compile(r"\bdoesn'?t\s+(?:cover|include|exist|have)\b", re.IGNORECASE),
    re.compile(r"\btopic\s+(?:is\s+)?not\s+(?:in|covered|available|included|found)\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+have\s+(?:enough\s+)?(?:information|knowledge|context|data)\b", re.IGNORECASE),
    re.compile(r"\bI'?m\s+(?:not\s+sure|unsure)\b", re.IGNORECASE),
    re.compile(r"\bunable\s+to\s+(?:answer|find|locate)\b", re.IGNORECASE),
    re.compile(r"\bcurriculum\s+(?:does\s+not|doesn'?t)\s+cover\b", re.IGNORECASE),
    re.compile(r"\bnot\s+part\s+of\s+(?:the\s+)?(?:curriculum|syllabus|knowledge)\b", re.IGNORECASE),
]


def _rag_says_no_answer(text: str | None) -> bool:
    """True when the RAG response is empty or indicates it can't answer."""
    if not text or len(text.strip()) < 5:
        return True
    return any(p.search(text) for p in _RAG_NO_ANSWER_PATTERNS)


def _strip_preamble(text: str) -> str:
    """Scrub forbidden openers, markdown formatting, and LaTeX-style math
    from an LLM reply. Loops so stacked openings ("Sure! According to the
    GCE…") all go."""
    if not text:
        return text
    cleaned = text.lstrip()
    for _ in range(5):
        prev = cleaned
        for pat in _PREAMBLE_PATTERNS:
            cleaned = pat.sub("", cleaned, count=1).lstrip()
        if cleaned == prev:
            break
    cleaned = _MARKDOWN_BOLD.sub(r"\1", cleaned)
    cleaned = _MARKDOWN_ITALIC.sub(r"\1", cleaned)
    cleaned = _MARKDOWN_HEADER.sub("", cleaned)
    # Convert LaTeX-flavoured math ($x^3$, H_2O, \pi, \frac{a}{b}, …) into
    # plain Unicode the chat UI can render verbatim.
    cleaned = clean_math_notation(cleaned)
    # If scrubbing removed everything, fall back to the original — better to
    # ship an imperfect answer than a blank one.
    return cleaned.strip() or text.strip()


LOCAL_ANSWER_PROMPT = """\
You are CARATI answering a {subject} question for a Cameroon GCE Advanced
Level (A-Level) student. Your goal: write the exact mark-earning answer the
student should put on the paper.

STRICT STYLE — FOLLOW EVERY RULE:

FORMAT (plain text only):
- No markdown. NEVER use **bold**, *italic*, # headers, > quotes, backticks,
  `*` / `-` bullets. Plain prose, paragraph breaks if needed.
- NO LaTeX. Never wrap maths in $...$, $$...$$, \\(, or \\[. Never emit
  \\frac, \\sqrt, \\pi, \\times, \\alpha, \\theta, etc. Use Unicode
  directly — x², x³, xⁿ, 10⁻³, H₂O, CO₂, π, √, ×, ±, α, Δ. Fractions are
  written inline as a slash: (x+1)/(x-1). Square roots as √(x+1).
- No preamble — this rule is non-negotiable. The first word of your reply
  must be the first word of the actual answer.

  FORBIDDEN openers (do not start with any of these or any close variant):
    "According to the GCE…"
    "According to the Cameroon GCE A-Level…"
    "As per the GCE / syllabus / curriculum…"
    "Based on the GCE…"
    "In the Cameroon GCE A-Level…"
    "For the GCE student…"
    "Here's the answer…"   "Here is your answer…"
    "Sure!"   "Of course!"   "Certainly!"   "Absolutely!"
    "Great question!"   "Happy to help!"
    "Let's explore…"   "Let me explain…"

  CORRECT example —
    Question: "what is electrolysis?"
    Reply MUST start: "Electrolysis is …"  (the definition itself)
    NEVER: "According to the GCE A-Level chemistry syllabus, electrolysis…"

- No meta commentary about paper type or syllabus position.

DEFINITIONS — one or two sentences, the exact GCE marking-scheme form:
- Include the key terms an A-Level examiner rewards. Drop everything else.
- Stop when the definition is complete — no padding, no applications,
  no "you will learn…", no history.

PROBLEM-SOLVING — strict GCE working layout:
1. Data: list every quantity with symbol and given value+unit, one per line.
   Mark unknown(s) clearly.
2. SI conversion: convert EVERY non-SI quantity to SI units BEFORE any
   calculation (cm -> m, g -> kg, minutes -> s, etc.). Show each conversion.
3. Formula: write it in symbols.
4. Substitution: substitute the SI values.
5. Result: state the final answer with correct SI unit and 3 significant
   figures (unless the question specifies otherwise).
6. For multi-part questions, label (a), (b), (c) and apply the same layout.

LENGTH: as short as possible while still earning the mark.

[CONVERSATION HISTORY]
{history}
[/CONVERSATION HISTORY]

[STUDENT QUESTION]
{question}
[/STUDENT QUESTION]

Reply with just the answer text. Plain text. No JSON. No preamble.
"""


async def _generate_local_answer(subject: str, question: str, history_text: str) -> str:
    """Fallback: produce a GCE-aligned answer directly via Gemini when the
    router didn't already include one in `reply`. Used only for non-physics
    academic queries where RAG is bypassed.
    """
    prompt = LOCAL_ANSWER_PROMPT.format(
        subject=subject or "general",
        history=history_text or "(no prior turns)",
        question=question or "",
    )
    try:
        resp = await asyncio.wait_for(
            async_openrouter_client.chat.completions.create(
                model=GEMINI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            ),
            timeout=ROUTER_TIMEOUT,
        )
        if resp.choices:
            return (resp.choices[0].message.content or "").strip() or FALLBACK_ANSWER
    except Exception as e:
        print(f"Local answer generation failed: {e}")
    return FALLBACK_ANSWER


async def _persist_message(
    http_client: httpx.AsyncClient,
    user_uuid: str | None,
    content: str,
    role: str,
    conversation_id: str | None = None,
):
    if not content:
        return
    userid = AI_TUTOR_USER_ID if role == AI_TUTOR_ROLE else user_uuid
    if not userid:
        # No resolved UUID for this caller — skip persistence rather than 422.
        print(f"Skipping persist for role={role}: no UUID resolved.")
        return
    payload = {"userid": userid, "message": content, "role": role}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    try:
        resp = await http_client.post(
            MESSAGES_URL,
            json=payload,
            timeout=PERSIST_TIMEOUT,
        )
        if resp.status_code >= 400:
            print(f"Persist {role} failed {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"Failed to persist {role} message: {e}")


async def _transcribe_audio(audio_bytes: bytes, filename: str | None, content_type: str | None) -> str:
    """Transcribe audio via Groq Whisper. Returns the plain transcript text."""
    name = filename or "audio.webm"
    mime = content_type or "audio/webm"
    file_tuple = (name, audio_bytes, mime)
    try:
        result = await asyncio.wait_for(
            async_groq_client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=file_tuple,
                response_format="text",
            ),
            timeout=TRANSCRIBE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print("Whisper transcription timed out")
        return ""
    except Exception as e:
        print(f"Whisper transcription failed: {e}")
        return ""
    # Groq with response_format='text' returns the plain string in result.text/.body
    if isinstance(result, str):
        return result.strip()
    return getattr(result, "text", "").strip()


async def _safe_redis(coro, default=None):
    try:
        return await asyncio.wait_for(coro, timeout=REDIS_TIMEOUT)
    except Exception as e:
        print(f"Redis op failed: {e}")
        return default

@router.post("")
async def master_controller(
    user_id: str = Form(...),
    message: str = Form(""),
    conversation_id: str = Form(""),
    file: UploadFile = File(None) # Optional file (Image or Audio)
):
    session_key = f"chat:history:{user_id}"
    # Drop anything that isn't a UUID — the messages.conversation_id column is
    # uuid, so non-UUID frontend fallbacks would 422 the persist insert.
    valid_conversation_id = conversation_id if UUID_RE.match(conversation_id or "") else None
    # Frontend may pass either a UUID or the user's phone number. Resolve once
    # so persistence (which requires a UUID) works without 422-ing.
    resolved_user_uuid = _resolve_user_uuid(user_id)

    # Tier gate: only enforce when we can identify the user. Anonymous /
    # unresolved callers fall through to the existing flow unchanged.
    if resolved_user_uuid:
        usage = _usage_snapshot(resolved_user_uuid)
        if usage["remaining"] <= 0:
            return JSONResponse(
                status_code=200,
                content={
                    "answer": (
                        f"You've reached your daily limit of {usage['limit']} messages "
                        f"on Tier {usage['tier']}. Your messages reset at midnight UTC, "
                        f"or upgrade your tier for more."
                    ),
                    "intent": "limit_exceeded",
                    "usage": usage,
                },
            )

    # Reject audio + text in the same request (text + image is fine).
    if (
        file
        and file.content_type
        and file.content_type.startswith("audio")
        and message
        and message.strip()
    ):
        return JSONResponse(
            status_code=200,
            content={
                "answer": "Please send audio on its own — audio can't be combined with text in the same message.",
                "intent": "error",
            },
        )

    try:
        # 1. Fetch History (non-fatal if Redis is down). Last 24 turns, oldest first.
        raw_history = await _safe_redis(redis_client.lrange(session_key, -24, -1), default=[]) or []
        history_list = []
        for m in raw_history:
            try:
                history_list.append(json.loads(m))
            except Exception:
                continue

        if history_list:
            history_lines = [
                f"{idx + 1}. {m.get('role', 'user')}: {m.get('content', '').strip()}"
                for idx, m in enumerate(history_list)
            ]
            history_text = "\n".join(history_lines)
        else:
            history_text = "(no prior turns — this is the start of the conversation)"

        # 2. Resolve uploaded file: transcribe audio with Whisper, or prep image for Gemini router.
        image_data = None
        transcript: str | None = None
        effective_message = (message or "").strip()

        if file:
            file_bytes = await file.read()
            mime = file.content_type or ""
            if mime.startswith("audio"):
                transcript = await _transcribe_audio(file_bytes, file.filename, mime)
                effective_message = transcript or "(audio could not be transcribed)"
            elif mime.startswith("image"):
                image_data = image_data_url(file_bytes, mime)
            # other content types are silently ignored

        if not effective_message and not image_data:
            effective_message = "(no text provided)"

        # 3. Build OpenAI-format messages for Gemini router.
        user_text_block = (
            f"[HISTORY — recent turns, oldest first]\n{history_text}\n[/HISTORY]\n\n"
            f"[NEW USER MESSAGE]\n{effective_message}"
        )
        if image_data:
            user_content = [
                {"type": "text", "text": user_text_block},
                {"type": "image_url", "image_url": {"url": image_data}},
            ]
        else:
            user_content = user_text_block

        messages = [
            {"role": "system", "content": ROUTER_PROMPT},
            {"role": "user", "content": user_content},
        ]

        # 4. Gemini 1.5 routing call (handles both text and image).
        try:
            intent_response = await asyncio.wait_for(
                async_openrouter_client.chat.completions.create(
                    model=GEMINI_MODEL,
                    messages=messages,
                    temperature=0.2,
                    response_format={"type": "json_object"},
                ),
                timeout=ROUTER_TIMEOUT,
            )
        except asyncio.TimeoutError:
            print("Gemini router router timed out")
            return JSONResponse(
                status_code=200,
                content={"answer": "That took too long to analyze. Please try again.", "intent": "error"},
            )
        except Exception as e:
            print(f"Gemini router router failed: {e}")
            return JSONResponse(
                status_code=200,
                content={"answer": FALLBACK_ANSWER, "intent": "error", "error": str(e)[:300]},
            )

        raw_text = "{}"
        if intent_response.choices:
            raw_text = intent_response.choices[0].message.content or "{}"
        try:
            intent_data = json.loads(raw_text)
        except Exception:
            intent_data = {}

        intent = intent_data.get("intent", "academic")
        subject = intent_data.get("subject", "general")
        teacher_reply = intent_data.get("reply")
        smart_query = intent_data.get("contextualized_query") or message or ""

        # Sticky-session override: /api/extract is multi-turn — it asks for
        # missing fields ("which year?") and stores partial state under
        # `extract:session:{user_id}` in Redis. When the student replies "2020"
        # or "Paper 2", Gemini will often classify that bare reply as
        # `academic`/`off_topic`, dropping them out of the extract flow halfway.
        # If a pending extract session exists, force `document` and forward the
        # raw reply (not Gemini's contextualised rewrite, which can mangle
        # short answers).
        try:
            extract_session_active = await _safe_redis(
                redis_client.exists(f"extract:session:{user_id}"),
                default=0,
            )
        except Exception:
            extract_session_active = 0
        if extract_session_active and intent != "document":
            # Only KEEP them in the extract flow if the bare reply looks like
            # a paper detail ("2020", "Paper 2", "yes"). If the new message
            # is clearly a real question, abandon the extract session and
            # trust Gemini's classification.
            if _looks_like_paper_detail(message or ""):
                print(f"Forcing document intent for {user_id} (extract session active, looks like paper detail)")
                intent = "document"
                raw_reply = (message or "").strip()
                if raw_reply:
                    smart_query = raw_reply
            else:
                print(f"Abandoning extract session for {user_id} (student moved on)")
                await _safe_redis(redis_client.delete(f"extract:session:{user_id}"))

        # 4. Save User Input to History (non-fatal)
        await _safe_redis(
            redis_client.rpush(session_key, json.dumps({"role": "user", "content": smart_query}))
        )

        # 5. Direct Reply vs RAG/Document Routing
        bot_answer = None
        api_data = {}
        async with httpx.AsyncClient(timeout=DOWNSTREAM_TIMEOUT) as http_client:
            await _persist_message(
                http_client, resolved_user_uuid, smart_query, "student",
                conversation_id=valid_conversation_id,
            )

            if intent == "off_topic":
                # Anything outside school work → warm redirect, no RAG, no
                # paper API. The LLM produces the reply in `teacher_reply`;
                # fall back to a canned nudge if it didn't.
                bot_answer = teacher_reply or (
                    "Let's keep our focus on school work — what subject "
                    "can I help you with today?"
                )
                bot_answer = _strip_preamble(bot_answer)
            elif intent == "document":
                # Past-paper retrieval — no LLM fallback; this needs the
                # actual paper file, not a generated answer.
                payload = {
                    "user_id": user_id,
                    "query": smart_query,
                    "subject": subject,
                    "class_level": HARDCODED_CLASS,
                    "school_id": HARDCODED_SCHOOL_ID,
                }
                try:
                    response = await http_client.post(EXTRACT_URL, json=payload)
                    if response.status_code != 200:
                        bot_answer = "I'm having a bit of trouble retrieving that paper right now."
                        api_data = {"error": response.text[:500]}
                    else:
                        api_data = response.json()
                        # /api/extract returns its reply in `message`.
                        bot_answer = (
                            api_data.get("response")
                            or api_data.get("answer")
                            or api_data.get("message")
                        )
                except (httpx.TimeoutException, httpx.RequestError) as e:
                    print(f"Downstream {EXTRACT_URL} failed: {e}")
                    bot_answer = "The paper service is slow or unavailable right now. Please try again."
                    api_data = {"error": str(e)}
            else:
                # Every academic query (any subject) → /api/rag. The RAG
                # endpoint handles its own knowledge-base lookup and LLM
                # fallback internally, so there's a single code path here.
                # We only need a last-resort fallback for the case where the
                # controller can't even reach /api/rag (network error /
                # timeout).
                rag_returned_answer = False
                payload = {
                    "user_id": user_id,
                    "message": smart_query,
                    "subject": subject,
                    "class_level": HARDCODED_CLASS,
                    "school_id": HARDCODED_SCHOOL_ID,
                }
                try:
                    response = await http_client.post(RAG_URL, json=payload)
                    if response.status_code == 200:
                        api_data = response.json()
                        raw = (
                            api_data.get("response")
                            or api_data.get("answer")
                            or api_data.get("message")
                        )
                        # RAG endpoint already runs its own KB→LLM fallback,
                        # so any non-empty answer is acceptable. The
                        # `_rag_says_no_answer` check is a last-line guard in
                        # case RAG itself slips through a "topic not covered"
                        # string for some reason.
                        if raw and not _rag_says_no_answer(raw):
                            bot_answer = raw
                            rag_returned_answer = True
                        else:
                            print(f"RAG returned no-answer for {subject}; controller-side fallback")
                    else:
                        print(f"RAG returned {response.status_code}: {response.text[:200]}")
                        api_data = {"error": response.text[:500]}
                except (httpx.TimeoutException, httpx.RequestError) as e:
                    print(f"Downstream {RAG_URL} failed: {e}; controller-side fallback")
                    api_data = {"error": str(e)}

                if not rag_returned_answer:
                    bot_answer = await _generate_local_answer(
                        subject=subject,
                        question=smart_query,
                        history_text=history_text,
                    )
                bot_answer = _strip_preamble(bot_answer)

            if not bot_answer:
                bot_answer = FALLBACK_ANSWER

            await _persist_message(
                http_client, resolved_user_uuid, bot_answer, AI_TUTOR_ROLE,
                conversation_id=valid_conversation_id,
            )

        # 6. Finalize History & Response (non-fatal)
        await _safe_redis(
            redis_client.rpush(session_key, json.dumps({"role": "assistant", "content": bot_answer}))
        )
        await _safe_redis(redis_client.ltrim(session_key, -24, -1))
        await _safe_redis(redis_client.expire(session_key, 86400))

        # Use the actual Whisper transcript we already produced (not smart_query,
        # which is the rewritten/contextualised version).
        transcription = transcript if (file and file.content_type and file.content_type.startswith("audio")) else None

        usage_after = _usage_snapshot(resolved_user_uuid) if resolved_user_uuid else None

        return {
            **api_data,
            "answer": bot_answer,
            "intent": intent,
            "transcription": transcription,
            "usage": usage_after,
        }

    except Exception as e:
        print(f"Master Controller Error: {type(e).__name__}: {e}")
        return JSONResponse(
            status_code=200,
            content={"answer": FALLBACK_ANSWER, "error": str(e), "intent": "error"},
        )


@router.get("/usage")
async def get_usage(user_id: str):
    """Return today's tier-limit snapshot for the given user (UUID or phone)."""
    resolved = _resolve_user_uuid(user_id)
    if not resolved:
        return JSONResponse(
            status_code=404,
            content={"error": "User not found", "user_id": user_id},
        )
    return _usage_snapshot(resolved)


# Frontend cap is 4; allow a little headroom so we never under-serve when the
# user widens it. Two-step query: find this user's recent conversation_ids,
# then fetch every message tagged with those IDs (this picks up bot replies,
# which are stored under AI_TUTOR_USER_ID, not the student's user_id).
CONVERSATIONS_LIMIT = 8
CONVERSATION_LOOKBACK_ROWS = 500
TITLE_MAX_CHARS = 40


def _derive_title(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") == "student" and (m.get("message") or "").strip():
            text = m["message"].strip()
            return (text[:TITLE_MAX_CHARS] + "…") if len(text) > TITLE_MAX_CHARS else text
    return "New chat"


@router.get("/conversations")
async def list_conversations(user_id: str, limit: int = CONVERSATIONS_LIMIT):
    """Reconstruct a user's recent conversations from the messages table."""
    resolved = _resolve_user_uuid(user_id)
    if not resolved:
        return JSONResponse(
            status_code=404,
            content={"error": "User not found", "user_id": user_id},
        )

    try:
        recent = (
            supabase.table("messages")
            .select("conversation_id, timestamp")
            .eq("userid", resolved)
            .eq("role", "student")
            .not_.is_("conversation_id", "null")
            .order("timestamp", desc=True)
            .limit(CONVERSATION_LOOKBACK_ROWS)
            .execute()
        )
    except Exception as e:
        print(f"Conversation lookup failed for {resolved}: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

    seen: list[str] = []
    last_activity: dict[str, str] = {}
    for row in recent.data or []:
        cid = row.get("conversation_id")
        if not cid:
            continue
        if cid not in last_activity:
            last_activity[cid] = row.get("timestamp", "")
            seen.append(cid)
        if len(seen) >= limit:
            break

    if not seen:
        return {"conversations": []}

    try:
        msgs_res = (
            supabase.table("messages")
            .select("conversation_id, message, role, timestamp")
            .in_("conversation_id", seen)
            .order("timestamp", desc=False)
            .execute()
        )
    except Exception as e:
        print(f"Bulk message fetch failed: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

    grouped: dict[str, list[dict]] = {cid: [] for cid in seen}
    for row in msgs_res.data or []:
        cid = row.get("conversation_id")
        if cid in grouped:
            grouped[cid].append(row)

    conversations = []
    for cid in seen:
        msgs = grouped.get(cid) or []
        if not msgs:
            continue
        conversations.append({
            "id": cid,
            "title": _derive_title(msgs),
            "createdAt": msgs[0].get("timestamp"),
            "updatedAt": last_activity.get(cid) or msgs[-1].get("timestamp"),
            "messages": [
                {
                    # Map server roles to frontend sender values.
                    "sender": "user" if m.get("role") == "student" else "bot",
                    "text": m.get("message") or "",
                    "timestamp": m.get("timestamp"),
                }
                for m in msgs
            ],
        })

    return {"conversations": conversations}