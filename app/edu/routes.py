"""
School / teacher / student app API (prefix /api/edu).

Auth is name + phone (no passwords). Student RAG isolation is handled by the
existing worker endpoints:
  - teacher note upload  -> POST /api/schools/{slug}/documents
  - student Q&A          -> POST /api/rag   (scoped by the student's school_id)
This module adds accounts/roles/enrollment and the assignments feature.
"""
import re
import uuid
import functools
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Depends, Form, File, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.db.supabase import get_supabase_client
from app.edu.auth import make_token, get_current_user, require_role

router = APIRouter(prefix="/api/edu", tags=["School App"])


def surface_errors(fn):
    """Turn unexpected exceptions into a 500 that includes the real message,
    so failures (e.g. a missing migration) are visible to the client instead
    of an opaque 'Internal Server Error'."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 — deliberately surface the detail
            raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    return wrapper


# ==========================================================================
# Helpers
# ==========================================================================
def _db():
    return get_supabase_client().client


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_user(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row.get("display_name"),
        "phone": row.get("phone_number"),
        "role": row.get("role"),
        "school_id": row.get("school_id"),
        "enrolled_by": row.get("enrolled_by"),
        # Self-reported school (informational only — everyone sits under "general").
        "origin_school": (row.get("preferences") or {}).get("origin_school"),
    }


def _get_user_by_phone(phone: str) -> Optional[dict]:
    res = _db().table("reg_users").select("*").eq("phone_number", phone).limit(1).execute()
    return res.data[0] if res.data else None


def _create_user(name: str, phone: str, role: str, school_id: Optional[str],
                 enrolled_by: Optional[str] = None,
                 preferences: Optional[dict] = None) -> dict:
    payload = {
        "display_name": name,
        "phone_number": phone,
        "role": role,
        "school_id": school_id,
        "enrolled_by": enrolled_by,
    }
    if preferences:
        payload["preferences"] = preferences
    res = _db().table("reg_users").insert(payload).execute()
    return res.data[0]


def _school_dict(school_id: Optional[str]) -> Optional[dict]:
    if not school_id:
        return None
    res = _db().table("reg_schools").select("id,name,slug").eq("id", school_id).limit(1).execute()
    return res.data[0] if res.data else None


def _auth_response(user: dict, school: Optional[dict] = None) -> dict:
    token = make_token(user["id"], user["role"], user.get("school_id"))
    if school is None:
        school = _school_dict(user.get("school_id"))
    return {"token": token, "user": _public_user(user), "school": school}


def _resolve_school(slug_or_name: str):
    school = get_supabase_client().get_school_by_slug(slug_or_name)
    if not school:
        raise HTTPException(status_code=404, detail=f"School '{slug_or_name}' not found")
    return school


GENERAL_SCHOOL_SLUG = "general"
GENERAL_SCHOOL_NAME = "General"


def _get_or_create_general_school():
    """The shared school every self-registered user belongs to. Created on
    first use so public registration never fails on a missing school."""
    client = get_supabase_client()
    school = client.get_school_by_slug(GENERAL_SCHOOL_SLUG)
    if school:
        return school
    from app.db.models import SchoolCreate
    return client.create_school(SchoolCreate(name=GENERAL_SCHOOL_NAME, slug=GENERAL_SCHOOL_SLUG))


# ==========================================================================
# Request models
# ==========================================================================
class LoginRequest(BaseModel):
    phone: str
    name: Optional[str] = Field(None, description="Optional; checked against the account name if given")


class RegisterRequest(BaseModel):
    name: str = Field(..., example="Bonam Osene")
    phone: str = Field(..., example="+254748404401")
    school: Optional[str] = Field(None, description="Self-reported school (informational only)", example="GBHS Molyko")


class EnrollStudent(BaseModel):
    name: str
    phone: str


class AssignmentCreate(BaseModel):
    title: str
    description: Optional[str] = None
    due_date: Optional[str] = Field(None, description="ISO datetime")
    subject: Optional[str] = Field(None, description="Subject slug or name")
    class_level: Optional[str] = Field(None, alias="class", description="Class name")
    student_ids: Optional[List[str]] = Field(None, description="Target students; all school students if omitted")

    class Config:
        populate_by_name = True


class SubmissionCreate(BaseModel):
    content: str


class GradeSubmission(BaseModel):
    grade: Optional[str] = None
    feedback: Optional[str] = None


class PracticeRequest(BaseModel):
    topic: Optional[str] = Field(None, description="Topic/subject; inferred from recent chat if omitted")
    count: int = Field(5, ge=1, le=15)


class TopUpRequest(BaseModel):
    amount: int = Field(..., ge=1, description="Amount to top up (in your local currency)")


# ==========================================================================
# AUTH
# ==========================================================================
@router.post("/register", status_code=201)
@surface_errors
async def register(payload: RegisterRequest):
    """Public sign-up. Creates a student under the shared 'general' school —
    every self-service user is registered here (name + phone, no password)."""
    name = payload.name.strip()
    phone = payload.phone.strip()
    if not name or not phone:
        raise HTTPException(status_code=400, detail="name and phone are required")
    if _get_user_by_phone(phone):
        raise HTTPException(status_code=409, detail="An account with this phone already exists. Please log in.")
    school = _get_or_create_general_school()
    prefs = None
    if payload.school and payload.school.strip():
        prefs = {"origin_school": payload.school.strip()}
    student = _create_user(name, phone, "student", str(school.id), preferences=prefs)
    return _auth_response(
        student,
        school={"id": str(school.id), "name": school.name, "slug": school.slug},
    )


@router.post("/login")
@surface_errors
async def login(payload: LoginRequest):
    """Log in with phone (name optional, verified if provided)."""
    user = _get_user_by_phone(payload.phone.strip())
    if not user:
        raise HTTPException(status_code=404, detail="No account found for this phone")
    if payload.name and (user.get("display_name") or "").strip().lower() != payload.name.strip().lower():
        raise HTTPException(status_code=401, detail="Name does not match this phone")
    return _auth_response(user)


@router.get("/me")
@surface_errors
async def me(user: dict = Depends(get_current_user)):
    return {"user": _public_user(user), "school": _school_dict(user.get("school_id"))}


# ==========================================================================
# CHAT — Carati study companion (auto subject detection + moral casual talk)
# ==========================================================================
CARATI_SYSTEM_PROMPT = """You are Carati, a warm, encouraging study companion for Cameroon GCE Advanced Level (A-Level) students.

DETECT THE SUBJECT YOURSELF from the question — never ask the student to pick a subject or class.

ANSWER DIRECTLY — do NOT interrogate the student. Never reply with a numbered checklist of clarifying questions (e.g. "which subject? which year? which paper?"). Make a sensible assumption and give a useful answer straight away. Ask at most ONE short question, as a single plain sentence, and only if you genuinely cannot proceed without it.

If a student asks for a specific past paper you don't have, don't quiz them — help immediately: explain the topic, or offer to generate practice questions in that exam's style.

FORMATTING — PLAIN TEXT ONLY. This is strict:
- NEVER use asterisks. No *word* and no **word**. No underscores for emphasis, no # headings, no backticks, no "-" or "*" bullet markers.
- No bold, no italics, no markdown of any kind. Write in ordinary sentences and short paragraphs.
- No LaTeX. Write maths with unicode (x², √, π, ×, ½, H₂O, →); fractions inline as (a+b)/c.

ACADEMIC QUESTIONS (any GCE subject: mathematics, physics, chemistry, biology, economics, geography, history, literature, computer science, etc.):
- Answer accurately and align to the Cameroon GCE A-Level syllabus and marking style.
- For problems show the working step by step: list data with units, write the formula, substitute, then the result to 3 significant figures with units.
- Be concise — give the mark-earning answer, not padding.

CASUAL CONVERSATION (greetings, small talk, how they feel):
- Reply warmly and briefly, like a supportive friend. Light conversation is welcome.

MORAL GUARDRAILS (always apply):
- Keep everything wholesome, respectful and age-appropriate. Never produce profane, sexual, violent, hateful, dishonest, or otherwise immoral content.
- If asked for something harmful, unethical, or inappropriate, gently decline and steer the student back to their studies or a positive topic.
- Encourage honest effort. Support learning and past-paper practice; never help cheat in a live exam.

You are a helpful tool — get the student what they need, kindly and quickly."""


_MD_BOLD = re.compile(r"\*\*([^*\n]+?)\*\*")
_MD_BOLD_U = re.compile(r"__([^_\n]+?)__")
_MD_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_MD_ITALIC_U = re.compile(r"(?<!\w)_([^_\n]+?)_(?!\w)")
_MD_HEADER = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_MD_BULLET = re.compile(r"^(\s*)[*\-+]\s+", re.MULTILINE)
_MD_CODE = re.compile(r"`([^`]+)`")


def _to_plain_text(text: str) -> str:
    """Strip markdown the model keeps emitting despite instructions —
    **bold**, *italics*, #headers, `code`, and *-bullets — so the chat shows
    clean plain text."""
    if not text:
        return text
    text = _MD_BOLD.sub(r"\1", text)
    text = _MD_BOLD_U.sub(r"\1", text)
    text = _MD_ITALIC.sub(r"\1", text)
    text = _MD_ITALIC_U.sub(r"\1", text)
    text = _MD_HEADER.sub("", text)
    text = _MD_BULLET.sub(r"\1• ", text)
    text = _MD_CODE.sub(r"\1", text)
    return text


def _save_message(user: dict, conversation_id: str, role: str, content: str) -> None:
    _db().table("reg_messages").insert({
        "user_id": user["id"],
        "school_id": user.get("school_id"),
        "role": role,
        "content": content,
        "conversation_id": conversation_id,
    }).execute()


async def _carati_reply(history: list, user_message: str, image_url: Optional[str] = None) -> str:
    """Generate a Carati reply via the shared OpenRouter/Gemini client. When an
    image data URL is given, it's sent alongside the text (Gemini is multimodal)."""
    from app.gateway.routes.llm_clients import async_openrouter_client, GEMINI_MODEL
    from app.gateway.routes.text_cleanup import clean_math_notation

    msgs = [{"role": "system", "content": CARATI_SYSTEM_PROMPT}]
    for h in history:
        role = h.get("role")
        if role in ("user", "assistant") and h.get("content"):
            msgs.append({"role": role, "content": h["content"]})

    if image_url:
        user_content = [
            {"type": "text", "text": user_message or "Please look at this and help me."},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]
    else:
        user_content = user_message
    msgs.append({"role": "user", "content": user_content})

    resp = await async_openrouter_client.chat.completions.create(
        model=GEMINI_MODEL, messages=msgs, temperature=0.5,
    )
    answer = (resp.choices[0].message.content or "").strip() if resp.choices else ""
    return _to_plain_text(clean_math_notation(answer)) if answer else "I'm not sure how to answer that — try rephrasing?"


def _extract_pdf_text(data: bytes) -> str:
    """Best-effort text extraction from a PDF (PyMuPDF)."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=data, filetype="pdf")
        return "\n".join(page.get_text() for page in doc).strip()
    except Exception:
        return ""


_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic")


@router.post("/chat")
@surface_errors
async def chat(
    message: str = Form(""),
    conversation_id: str = Form(""),
    file: UploadFile = File(None),
    user: dict = Depends(get_current_user),
):
    """Send a message and/or an attachment (photo, image, or PDF). Carati reads
    the attachment, detects the subject, and replies with the chat as context.
    Casual talk is handled morally. Stores the exchange for 'past chats'."""
    text = (message or "").strip()
    conv_id = (conversation_id or "").strip() or str(uuid.uuid4())

    image_url: Optional[str] = None
    attachment_note: Optional[str] = None
    if file is not None and file.filename:
        data = await file.read()
        mime = (file.content_type or "").lower()
        fname = file.filename
        low = fname.lower()
        if mime.startswith("image") or low.endswith(_IMAGE_EXTS):
            from app.gateway.routes.llm_clients import image_data_url
            image_url = image_data_url(data, mime or "image/jpeg")
            attachment_note = f"📷 {fname}"
        elif mime == "application/pdf" or low.endswith(".pdf"):
            pdf_text = _extract_pdf_text(data)
            if pdf_text:
                text = (text + "\n\n" if text else "") + f"[Attached document '{fname}']\n{pdf_text[:8000]}"
            attachment_note = f"📄 {fname}"
        else:
            # Try as plain text; otherwise just note it.
            try:
                snippet = data.decode("utf-8", errors="ignore").strip()
                if snippet:
                    text = (text + "\n\n" if text else "") + f"[Attached file '{fname}']\n{snippet[:8000]}"
            except Exception:
                pass
            attachment_note = f"📎 {fname}"

    if not text and image_url is None and attachment_note is None:
        raise HTTPException(status_code=400, detail="Type a message or attach a file.")

    history = (
        _db().table("reg_messages").select("role,content,created_at")
        .eq("user_id", user["id"]).eq("conversation_id", conv_id)
        .order("created_at").limit(20).execute().data
    )
    answer = await _carati_reply(history, text or "Please help me with this.", image_url=image_url)

    # Store the student's turn — the original caption plus a short attachment note
    # (not the full extracted PDF dump), so history stays clean and readable.
    stored_user = (message or "").strip()
    if attachment_note:
        stored_user = (stored_user + "  " if stored_user else "") + attachment_note
    _save_message(user, conv_id, "user", stored_user or "(attachment)")
    _save_message(user, conv_id, "assistant", answer)
    return {"conversation_id": conv_id, "answer": answer}


@router.get("/chats")
@surface_errors
async def list_chats(user: dict = Depends(get_current_user)):
    """List the student's past conversations, newest first."""
    rows = (
        _db().table("reg_messages").select("conversation_id,role,content,created_at")
        .eq("user_id", user["id"]).order("created_at", desc=True).limit(500).execute().data
    )
    convs: dict = {}
    for r in rows:
        cid = r.get("conversation_id")
        if not cid:
            continue
        c = convs.get(cid)
        if c is None:
            c = {"id": cid, "title": "New chat", "updated_at": r.get("created_at")}
            convs[cid] = c
        # rows are newest-first, so the LAST user row seen is the opening question.
        if r.get("role") == "user" and r.get("content"):
            t = r["content"].strip()
            c["title"] = (t[:40] + "…") if len(t) > 40 else t
    return {"chats": list(convs.values())}


@router.get("/chats/{conversation_id}")
@surface_errors
async def get_chat(conversation_id: str, user: dict = Depends(get_current_user)):
    rows = (
        _db().table("reg_messages").select("role,content,created_at")
        .eq("user_id", user["id"]).eq("conversation_id", conversation_id)
        .order("created_at").execute().data
    )
    return {"conversation_id": conversation_id, "messages": rows}


@router.post("/practice")
@surface_errors
async def practice(payload: PracticeRequest, user: dict = Depends(get_current_user)):
    """Generate a short GCE practice test. Topic is inferred from the student's
    latest question when not provided."""
    topic = (payload.topic or "").strip()
    if not topic:
        last = (
            _db().table("reg_messages").select("content")
            .eq("user_id", user["id"]).eq("role", "user")
            .order("created_at", desc=True).limit(1).execute().data
        )
        topic = last[0]["content"].strip() if last else "general revision"

    prompt = (
        f"Create a practice test of {payload.count} Cameroon GCE A-Level questions on: {topic}. "
        "Number each question 1., 2., 3.… After all the questions, add a line 'ANSWERS' followed by "
        "concise worked answers for each. Plain text only, no markdown or LaTeX; use unicode for maths."
    )
    text = await _carati_reply([], prompt)
    return {"topic": topic, "practice": text}


def _render_chat_pdf(user: dict, rows: list) -> bytes:
    """Render a conversation into a nicely formatted PDF (reportlab)."""
    from io import BytesIO
    from xml.sax.saxutils import escape
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable

    green = colors.HexColor("#2e7d32")
    green_dark = colors.HexColor("#1b5e20")
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Ct", parent=styles["Title"], textColor=green, fontSize=22, spaceAfter=2)
    meta_style = ParagraphStyle("Cm", parent=styles["Normal"], textColor=colors.grey, fontSize=9, spaceAfter=2)
    you_label = ParagraphStyle("Cyl", parent=styles["Normal"], textColor=green_dark, fontName="Helvetica-Bold", fontSize=9, spaceBefore=12)
    car_label = ParagraphStyle("Ccl", parent=styles["Normal"], textColor=green, fontName="Helvetica-Bold", fontSize=9, spaceBefore=12)
    body_style = ParagraphStyle("Cb", parent=styles["Normal"], fontSize=11, leading=15.5, spaceBefore=2)

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title="Carati chat",
    )
    story = [
        Paragraph("Carati", title_style),
        Paragraph("GCE A-Level study chat", meta_style),
        Paragraph(f"{escape(user.get('display_name') or 'Student')} · {len(rows)} messages", meta_style),
        Spacer(1, 6),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#d6e8d6")),
    ]
    for r in rows:
        content = escape((r.get("content") or "").strip()).replace("\n", "<br/>")
        if not content:
            continue
        if r.get("role") == "user":
            story.append(Paragraph("You", you_label))
        else:
            story.append(Paragraph("Carati", car_label))
        story.append(Paragraph(content, body_style))
    doc.build(story)
    return buf.getvalue()


@router.get("/chats/{conversation_id}/pdf")
@surface_errors
async def chat_pdf(conversation_id: str, user: dict = Depends(get_current_user)):
    """Download a conversation as a formatted PDF."""
    rows = (
        _db().table("reg_messages").select("role,content,created_at")
        .eq("user_id", user["id"]).eq("conversation_id", conversation_id)
        .order("created_at").execute().data
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Chat not found")
    pdf = _render_chat_pdf(user, rows)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="carati-chat.pdf"'},
    )


@router.post("/topup")
@surface_errors
async def topup(payload: TopUpRequest, user: dict = Depends(get_current_user)):
    """Record a top-up request. NOTE: no payment provider is wired yet — this
    acknowledges the request so the flow works end to end; connect MTN MoMo /
    Orange Money / a gateway here to actually collect payment."""
    return {
        "status": "pending",
        "amount": payload.amount,
        "message": (
            f"Top-up of {payload.amount} noted. Mobile-money payment is being set "
            "up — you'll be able to complete it right here shortly."
        ),
    }


# ==========================================================================
# ROSTER / ENROLLMENT
# ==========================================================================
@router.post("/students", status_code=201)
@surface_errors
async def enroll_student(payload: EnrollStudent, teacher: dict = Depends(require_role("teacher", "school_admin"))):
    """Teacher/admin enrolls a student into their school."""
    phone = payload.phone.strip()
    existing = _get_user_by_phone(phone)
    if existing:
        raise HTTPException(status_code=409, detail="A user with this phone already exists")
    student = _create_user(
        payload.name.strip(), phone, "student",
        teacher.get("school_id"), enrolled_by=teacher["id"],
    )
    return {"student": _public_user(student)}


@router.get("/students")
@surface_errors
async def list_students(mine: bool = False, user: dict = Depends(require_role("teacher", "school_admin"))):
    """List students in the caller's school (mine=true → only those I enrolled)."""
    q = _db().table("reg_users").select("*").eq("role", "student").eq("school_id", user.get("school_id"))
    if mine:
        q = q.eq("enrolled_by", user["id"])
    res = q.order("created_at", desc=True).execute()
    return {"students": [_public_user(r) for r in res.data]}


@router.get("/teachers")
@surface_errors
async def list_teachers(user: dict = Depends(require_role("school_admin"))):
    """School admin lists teachers in the school."""
    res = (
        _db().table("reg_users").select("*")
        .eq("role", "teacher").eq("school_id", user.get("school_id"))
        .order("created_at", desc=True).execute()
    )
    return {"teachers": [_public_user(r) for r in res.data]}


# ==========================================================================
# ASSIGNMENTS
# ==========================================================================
def _resolve_subject_class(subject: Optional[str], class_level: Optional[str]):
    """Best-effort resolution of subject slug/name and class name to UUIDs."""
    subject_id = class_id = None
    if subject:
        s = get_supabase_client().get_subject_by_slug(subject)
        if s:
            subject_id = str(s.id)
            if class_level:
                for c in get_supabase_client().get_classes_by_subject(s.id):
                    if class_level.lower() in c.name.lower():
                        class_id = str(c.id)
                        break
    return subject_id, class_id


@router.post("/assignments", status_code=201)
@surface_errors
async def create_assignment(payload: AssignmentCreate, teacher: dict = Depends(require_role("teacher", "school_admin"))):
    """Create an assignment and target students (all school students if none given)."""
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="title is required")
    subject_id, class_id = _resolve_subject_class(payload.subject, payload.class_level)

    row = _db().table("reg_assignments").insert({
        "school_id": teacher.get("school_id"),
        "teacher_id": teacher["id"],
        "subject_id": subject_id,
        "class_id": class_id,
        "title": payload.title.strip(),
        "description": payload.description,
        "due_date": payload.due_date,
    }).execute().data[0]

    # Resolve targets.
    if payload.student_ids:
        student_ids = payload.student_ids
    else:
        res = (_db().table("reg_users").select("id")
               .eq("role", "student").eq("school_id", teacher.get("school_id")).execute())
        student_ids = [r["id"] for r in res.data]

    if student_ids:
        _db().table("reg_assignment_targets").insert(
            [{"assignment_id": row["id"], "student_id": sid} for sid in student_ids]
        ).execute()

    return {"assignment": row, "targeted": len(student_ids)}


@router.get("/assignments")
@surface_errors
async def list_assignments(user: dict = Depends(get_current_user)):
    """Teachers/admins see assignments they own; students see ones targeted to them."""
    db = _db()
    if user.get("role") == "student":
        tgt = db.table("reg_assignment_targets").select("assignment_id").eq("student_id", user["id"]).execute()
        ids = [t["assignment_id"] for t in tgt.data]
        if not ids:
            return {"assignments": []}
        res = db.table("reg_assignments").select("*").in_("id", ids).order("created_at", desc=True).execute()
        # attach this student's submission status
        subs = {s["assignment_id"]: s for s in
                db.table("reg_assignment_submissions").select("*").eq("student_id", user["id"]).execute().data}
        out = [{**a, "my_submission": subs.get(a["id"])} for a in res.data]
        return {"assignments": out}

    q = db.table("reg_assignments").select("*").eq("school_id", user.get("school_id"))
    if user.get("role") == "teacher":
        q = q.eq("teacher_id", user["id"])
    res = q.order("created_at", desc=True).execute()
    return {"assignments": res.data}


@router.get("/assignments/{assignment_id}")
@surface_errors
async def get_assignment(assignment_id: str, user: dict = Depends(get_current_user)):
    db = _db()
    res = db.table("reg_assignments").select("*").eq("id", assignment_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Assignment not found")
    assignment = res.data[0]
    if str(assignment.get("school_id")) != str(user.get("school_id")):
        raise HTTPException(status_code=403, detail="Not in your school")

    if user.get("role") == "student":
        sub = (db.table("reg_assignment_submissions").select("*")
               .eq("assignment_id", assignment_id).eq("student_id", user["id"]).limit(1).execute())
        assignment["my_submission"] = sub.data[0] if sub.data else None
    return {"assignment": assignment}


@router.post("/assignments/{assignment_id}/submit")
@surface_errors
async def submit_assignment(assignment_id: str, payload: SubmissionCreate,
                            student: dict = Depends(require_role("student"))):
    db = _db()
    # Ensure this assignment is actually targeted at the student.
    tgt = (db.table("reg_assignment_targets").select("id")
           .eq("assignment_id", assignment_id).eq("student_id", student["id"]).limit(1).execute())
    if not tgt.data:
        raise HTTPException(status_code=403, detail="This assignment is not assigned to you")

    existing = (db.table("reg_assignment_submissions").select("id")
                .eq("assignment_id", assignment_id).eq("student_id", student["id"]).limit(1).execute())
    data = {
        "assignment_id": assignment_id,
        "student_id": student["id"],
        "content": payload.content,
        "status": "submitted",
        "submitted_at": _now_iso(),
    }
    if existing.data:
        row = db.table("reg_assignment_submissions").update(data).eq("id", existing.data[0]["id"]).execute().data[0]
    else:
        row = db.table("reg_assignment_submissions").insert(data).execute().data[0]
    return {"submission": row}


@router.get("/assignments/{assignment_id}/submissions")
@surface_errors
async def list_submissions(assignment_id: str, teacher: dict = Depends(require_role("teacher", "school_admin"))):
    db = _db()
    res = db.table("reg_assignments").select("teacher_id, school_id").eq("id", assignment_id).limit(1).execute()
    if not res.data or str(res.data[0]["school_id"]) != str(teacher.get("school_id")):
        raise HTTPException(status_code=404, detail="Assignment not found")
    subs = db.table("reg_assignment_submissions").select("*").eq("assignment_id", assignment_id).execute()
    return {"submissions": subs.data}


@router.post("/submissions/{submission_id}/grade")
@surface_errors
async def grade_submission(submission_id: str, payload: GradeSubmission,
                           teacher: dict = Depends(require_role("teacher", "school_admin"))):
    db = _db()
    row = db.table("reg_assignment_submissions").update({
        "grade": payload.grade,
        "feedback": payload.feedback,
        "status": "graded",
        "graded_at": _now_iso(),
    }).eq("id", submission_id).execute()
    if not row.data:
        raise HTTPException(status_code=404, detail="Submission not found")
    return {"submission": row.data[0]}
