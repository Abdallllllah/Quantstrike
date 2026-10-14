"""
Student-facing Carati API (prefix /api/edu): sign-in, onboarding, Q&A,
past papers, profile, wallet. Teacher tracks for students live in tracks.py.
"""
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.edu import (allowance, chat_service, config, otp, papers_service, payments, profile,
                     topics)
from app.edu.api.common import api, error, read_attachment
from app.edu.auth import get_current_user, make_token
from app.edu.db import db, general_school_id, one, run
from app.edu.phone import normalize_phone

router = APIRouter(prefix="/api/edu", tags=["Carati — student"])


# ==========================================================================
# Sign-in and onboarding (FR-S4)
# ==========================================================================
class PhoneIn(BaseModel):
    phone: str = Field(..., example="+237 6 70 00 00 00")


class VerifyIn(BaseModel):
    phone: str
    code: Optional[str] = Field(None, description="Required when OTP is switched on")


class ProfileIn(BaseModel):
    level: str = Field(..., description="lower_sixth | upper_sixth")
    subjects: List[str] = Field(..., description="Subject slugs, e.g. ['physics','math']")
    school: str
    region: str


def public_user(u: dict) -> dict:
    return {
        "id": u["id"], "phone": u["phone_number"], "role": u["role"],
        "level": u.get("level"), "subjects": u.get("subjects") or [], "school": u.get("school_name"),
        "region": u.get("region"), "exam_year": u.get("exam_year"),
        "needs_onboarding": not u.get("onboarded_at"),
    }


def _find_or_create(phone: str) -> dict:
    u = one(db().table("reg_users").select("*").eq("phone_number", phone))
    if u:
        return u
    return db().table("reg_users").insert({
        "phone_number": phone, "role": "student", "school_id": general_school_id(),
    }).execute().data[0]


@router.get("/subjects")
@api
async def list_subjects():
    """The launch subjects (GCE A-Level, Anglophone)."""
    return {"subjects": await run(topics.subjects)}


@router.post("/auth/start")
@api
async def auth_start(body: PhoneIn):
    """Step 1: enter a phone number. Sends a code when OTP is on."""
    phone = normalize_phone(body.phone)
    if not phone:
        raise ValueError("Enter a valid phone number, e.g. +237 6XX XXX XXX")
    if config.OTP_REQUIRED:
        await run(otp.request_code, phone)
    return {"phone": phone, "otp_required": config.OTP_REQUIRED}


@router.post("/auth/verify")
@api
async def auth_verify(body: VerifyIn):
    """Step 2: confirm the code (if OTP is on) and get a token. New phone
    numbers get an account here; they then complete onboarding."""
    phone = normalize_phone(body.phone)
    if not phone:
        raise ValueError("Enter a valid phone number")
    if config.OTP_REQUIRED and not await run(otp.verify_code, phone, body.code or ""):
        return error(401, "bad_code", "That code is wrong or has expired")
    user = await run(_find_or_create, phone)
    return {"token": make_token(user["id"], user["role"], user.get("school_id")), "user": public_user(user)}


@router.get("/me")
@api
async def me(user: dict = Depends(get_current_user)):
    return {"user": public_user(user), "allowance": await run(allowance.status, user["id"])}


@router.put("/me/profile")
@api
async def update_profile(body: ProfileIn, user: dict = Depends(get_current_user)):
    """Onboarding: level, subjects, school, region. Exam year is inferred."""
    if body.level not in ("lower_sixth", "upper_sixth"):
        raise ValueError("level must be lower_sixth or upper_sixth")
    valid = {s["slug"] for s in topics.subjects()}
    chosen = [s for s in dict.fromkeys(body.subjects) if s in valid]
    if not chosen:
        raise ValueError("Choose at least one subject")
    row = {
        "level": body.level, "subjects": chosen, "school_name": body.school.strip()[:160],
        "region": body.region.strip()[:80],
        "exam_year": config.infer_exam_year(body.level, allowance.wat_now().date()),
        "onboarded_at": user.get("onboarded_at") or "now",
    }
    updated = await run(lambda: db().table("reg_users").update(row).eq("id", user["id"]).execute().data[0])
    return {"user": public_user(updated)}


@router.get("/allowance")
@api
async def get_allowance(user: dict = Depends(get_current_user)):
    """The always-visible counter: "11 of 15 left today", papers, credits, reset time."""
    return await run(allowance.status, user["id"])


# ==========================================================================
# Questions and answers (FR-S1)
# ==========================================================================
@router.post("/chat")
@api
async def chat(
    message: str = Form(""),
    conversation_id: str = Form(""),
    client_ref: str = Form("", description="Idempotency key; resend the same one on retry"),
    file: UploadFile = File(None),
    user: dict = Depends(get_current_user),
):
    """Ask a question (text and/or a photo). Returns the answer, its message id
    (for "still unsure" and ratings) and whether it counted. 429
    `daily_cap_reached` when a new question is asked with nothing left."""
    image_url, attach_text, note = await read_attachment(file)
    if not message.strip() and not image_url and not attach_text:
        raise ValueError("Type a question or attach a photo")
    reply = await chat_service.ask(
        user, message, conversation_id=conversation_id.strip() or None, image_url=image_url,
        attachment_text=attach_text, attachment_note=note, client_ref=client_ref.strip() or None,
    )
    return {**reply, "allowance": await run(allowance.status, user["id"])}


@router.post("/messages/{message_id}/unsure")
@api
async def still_unsure(message_id: str, user: dict = Depends(get_current_user)):
    """"Still unsure": a simpler re-explanation. Never counts against the allowance."""
    return await chat_service.still_unsure(user, message_id)


class RatingIn(BaseModel):
    value: int = Field(..., description="1 = thumbs up, -1 = thumbs down")


@router.post("/messages/{message_id}/rating")
@api
async def rate(message_id: str, body: RatingIn, user: dict = Depends(get_current_user)):
    return await chat_service.rate(user, message_id, body.value)


@router.get("/chats")
@api
async def list_chats(user: dict = Depends(get_current_user)):
    rows = await run(lambda: db().table("reg_messages").select("conversation_id,role,content,created_at,is_reexplanation")
                     .eq("user_id", user["id"]).order("created_at", desc=True).limit(800).execute().data or [])
    convs: dict = {}
    for r in rows:
        cid = r.get("conversation_id")
        if not cid:
            continue
        c = convs.setdefault(cid, {"id": cid, "title": "New chat", "updated_at": r["created_at"]})
        if r["role"] == "user" and not r.get("is_reexplanation") and r.get("content"):
            t = r["content"].strip()
            c["title"] = (t[:40] + "…") if len(t) > 40 else t
    return {"chats": list(convs.values())}


@router.get("/chats/{conversation_id}")
@api
async def get_chat(conversation_id: str, user: dict = Depends(get_current_user)):
    rows = await run(lambda: db().table("reg_messages")
                     .select("id,role,content,created_at,rating,unsure,is_reexplanation,cached,counts_against_allowance,"
                             "topic_id,reg_syllabus_topics!reg_messages_topic_id_fkey(name)")
                     .eq("user_id", user["id"]).eq("conversation_id", conversation_id)
                     .order("created_at").execute().data or [])
    for r in rows:
        r["topic"] = (r.pop("reg_syllabus_topics", None) or {}).get("name")
    return {"conversation_id": conversation_id, "messages": rows}


@router.get("/chats/{conversation_id}/pdf")
@api
async def chat_pdf(conversation_id: str, user: dict = Depends(get_current_user)):
    from app.edu.chat_pdf import render_chat_pdf
    rows = await run(lambda: db().table("reg_messages").select("role,content,created_at")
                     .eq("user_id", user["id"]).eq("conversation_id", conversation_id)
                     .order("created_at").execute().data)
    if not rows:
        raise LookupError("Chat not found")
    return Response(content=render_chat_pdf(rows), media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="carati-chat.pdf"'})


class PracticeIn(BaseModel):
    topic: Optional[str] = Field(None, description="Inferred from the latest question if omitted")
    count: int = Field(5, ge=1, le=15)


@router.post("/practice")
@api
async def practice(body: PracticeIn, user: dict = Depends(get_current_user)):
    """Generate a short practice test. Counts as one question."""
    topic = (body.topic or "").strip()
    if not topic:
        last = await run(lambda: one(db().table("reg_messages").select("content").eq("user_id", user["id"])
                                     .eq("role", "user").eq("is_reexplanation", False).order("created_at", desc=True)))
        topic = (last or {}).get("content", "").strip() or "general A-Level revision"
    if not await run(allowance.can_spend, user["id"], "question", None):
        raise chat_service.CapReached("question")
    from app.edu import llm
    text = chat_service._clean(await llm.complete([
        {"role": "system", "content": chat_service.SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Create a practice test of {body.count} Cameroon GCE A-Level questions on: {topic}. "
            "Number each question. After all the questions, add a line 'ANSWERS' followed by concise worked "
            "answers with the marks each step earns. Plain text, unicode maths.")},
    ], max_tokens=2500))
    await run(allowance.consume, user["id"], "question", f"practice:{uuid.uuid4()}", None)
    return {"topic": topic, "practice": text, "allowance": await run(allowance.status, user["id"])}


# ==========================================================================
# Past paper bank (FR-S2)
# ==========================================================================
@router.get("/papers")
@api
async def search_papers(q: str = "", subject: str = "", year: Optional[int] = None,
                        paper: Optional[int] = None, topic: str = "", limit: int = 50,
                        user: dict = Depends(get_current_user)):
    """Search by subject, year, paper number and topic. Browsing is free."""
    return await run(papers_service.search, user["id"], q, subject, year, paper, topic, limit)


@router.get("/papers/{paper_id}")
@api
async def get_paper(paper_id: str, user: dict = Depends(get_current_user)):
    """One paper's details and this student's state. Free."""
    return await run(papers_service.paper_view, user["id"], paper_id)


@router.post("/papers/{paper_id}/open")
@api
async def open_paper(paper_id: str, user: dict = Depends(get_current_user)):
    """Open a paper in full. Spends one paper unless already unlocked today or
    completed before. 429 `daily_cap_reached` at the cap."""
    result = await run(papers_service.open_paper, user["id"], paper_id)
    return {**result, "allowance": await run(allowance.status, user["id"])}


@router.get("/papers/{paper_id}/questions")
@api
async def paper_questions(paper_id: str, user: dict = Depends(get_current_user)):
    """The paper's questions as text, one per item (read on first use)."""
    if not await run(papers_service.has_access, user["id"], paper_id):
        raise PermissionError("Open the paper first")
    return {"paper_id": paper_id, "questions": await papers_service.ensure_questions(paper_id)}


@router.get("/papers/{paper_id}/download")
@api
async def download_paper(paper_id: str, user: dict = Depends(get_current_user)):
    if not await run(papers_service.can_download, user["id"], paper_id):
        raise PermissionError("Open the paper first (today), or complete it to keep access")
    try:
        data, name = await run(papers_service.download, paper_id)
    except LookupError:
        raise
    except Exception:  # noqa: BLE001
        raise LookupError("The file for this paper is missing from storage")
    safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in name)
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{safe}"'})


@router.get("/papers/{paper_id}/attempts")
@api
async def paper_attempts(paper_id: str, user: dict = Depends(get_current_user)):
    """Review earlier attempts and their marking — always allowed, even at the cap."""
    return {"attempts": await run(papers_service.attempts_for_paper, user["id"], paper_id)}


@router.post("/questions/{question_id}/attempts")
@api
async def attempt_question(
    question_id: str,
    answer: str = Form(""),
    track_id: str = Form(""),
    teacher_marking: bool = Form(False, description="Inside a track: ask the teacher to mark (capped per week)"),
    file: UploadFile = File(None),
    user: dict = Depends(get_current_user),
):
    """Submit an answer (text and/or photo) and get examiner-style marking."""
    image_url, attach_text, _ = await read_attachment(file)
    text = (answer + ("\n\n" + attach_text if attach_text else "")).strip()
    if not text and not image_url:
        raise ValueError("Write an answer or attach a photo of your working")
    return await papers_service.attempt(user, question_id, text, image_url,
                                        track_id=track_id or None, want_teacher=teacher_marking)


# ==========================================================================
# Profile (FR-S3)
# ==========================================================================
@router.get("/profile")
@api
async def my_profile(user: dict = Depends(get_current_user)):
    """Heat map per subject: strong / weak / learning / untouched topics."""
    return await run(profile.full_profile, user)


@router.get("/profile/{subject}")
@api
async def my_subject_profile(subject: str, user: dict = Depends(get_current_user)):
    s = topics.subject_by_any(subject)
    if not s:
        raise LookupError("Unknown subject")
    return {"subject": s, **await run(profile.subject_map, user["id"], s["id"])}


# ==========================================================================
# Wallet and top-ups (FR-S5)
# ==========================================================================
@router.get("/wallet")
@api
async def wallet(user: dict = Depends(get_current_user)):
    return {
        "allowance": await run(allowance.status, user["id"]),
        "products": payments.catalogue(),
        "payments": await run(payments.history, user["id"]),
    }


class PurchaseIn(BaseModel):
    product_code: str
    parent_phone: str = Field(..., description="The parent's MoMo / Orange Money number; receives the push and receipt")


@router.post("/wallet/purchase")
@api
async def purchase(body: PurchaseIn, user: dict = Depends(get_current_user)):
    return await run(payments.buy_topup, user, body.product_code, body.parent_phone)
