"""
Carati admin API (prefix /api/edu/admin) — Carati team only (role 'admin').

Content (FR-A1): library uploads, past-paper catalogue and question
extraction, syllabus topics, answer-review queue. People and money: teacher
vetting (FR-V1/V2), payment confirmation (until an aggregator webhook is
wired), payouts (FR-M2). Analytics (FR-A3).
"""
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field

from app.edu import allowance, config, papers_service, payments, topics
from app.edu.api.common import api
from app.edu.auth import get_current_user
from app.edu.db import db, general_school_id, one, run


async def _admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Carati admins only")
    return user


router = APIRouter(prefix="/api/edu/admin", tags=["Carati — admin"], dependencies=[Depends(_admin)])


# ==========================================================================
# Library (corpus) and past papers
# ==========================================================================
@router.get("/documents")
@api
async def documents(subject: str = ""):
    q = db().table("reg_documents").select("id,filename,doc_type,subject_id,chunk_count,is_indexed,created_at") \
        .eq("school_id", general_school_id())
    s = topics.subject_by_any(subject) if subject else None
    if s:
        q = q.eq("subject_id", s["id"])
    return {"documents": await run(lambda: q.order("created_at", desc=True).limit(2000).execute().data or [])}


@router.post("/documents", status_code=201)
@api
async def upload_document(
    file: UploadFile = File(...),
    subject: str = Form(..., description="Launch subject slug, e.g. physics"),
    doc_type: str = Form("notes", description="notes | syllabus | past_paper | marking_scheme"),
):
    """Add a PDF to the library: chunked, embedded and searchable by the chat.
    Past papers / marking schemes also appear in the paper bank after a sync."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise ValueError("Only PDF files are accepted")
    s = topics.subject_by_any(subject)
    if not s:
        raise ValueError("Unknown subject")
    data = await file.read()
    Path("uploads").mkdir(exist_ok=True)
    path = Path("uploads") / Path(file.filename).name
    path.write_bytes(data)

    def class_id():
        row = one(db().table("reg_classes").select("id").eq("subject_id", s["id"]).eq("name", "A-Level"))
        return row["id"] if row else db().table("reg_classes").insert(
            {"name": "A-Level", "subject_id": s["id"]}).execute().data[0]["id"]

    from app.rag.ingestion import get_ingestion_service
    ok, msg = await get_ingestion_service().ingest_document(
        file_path=str(path), school_id=general_school_id(), subject_id=s["id"],
        class_id=await run(class_id), doc_type=doc_type,
    )
    if not ok:
        raise RuntimeError(msg)
    synced = await run(papers_service.sync_papers) if doc_type in ("past_paper", "marking_scheme") else None
    return {"message": msg, "subject": s["name"], "papers_synced": synced}


@router.delete("/documents/{document_id}")
@api
async def delete_document(document_id: str):
    doc = await run(lambda: one(db().table("reg_documents").select("id,filename").eq("id", document_id)))
    if not doc:
        raise LookupError("Document not found")
    await run(lambda: db().rpc("reg_delete_document_embeddings", {"doc_id": document_id}).execute())
    await run(lambda: db().table("reg_documents").delete().eq("id", document_id).execute())
    return {"deleted": doc["filename"]}


@router.get("/papers")
@api
async def all_papers(include_inactive: bool = True):
    """Every catalogued paper, including hidden ones (so they can be switched back on)."""
    q = db().table("reg_past_papers").select(
        "id,subject_id,filename,title,years,paper_numbers,kind,is_active,questions_extracted_at")
    if not include_inactive:
        q = q.eq("is_active", True)
    return {"papers": await run(lambda: q.order("filename").limit(5000).execute().data or [])}


@router.post("/papers/sync")
@api
async def sync_papers():
    """Re-scan the library and update the past paper bank."""
    return await run(papers_service.sync_papers)


@router.post("/papers/{paper_id}/extract")
@api
async def extract_questions(paper_id: str):
    """(Re)read a paper's questions now instead of on first use."""
    await run(lambda: db().table("reg_past_papers").update({"questions_extracted_at": None}).eq("id", paper_id).execute())
    return {"questions": await papers_service.ensure_questions(paper_id)}


class PaperPatch(BaseModel):
    title: Optional[str] = None
    subject_id: Optional[str] = None
    years: Optional[list] = None
    paper_numbers: Optional[list] = None
    kind: Optional[str] = None
    is_active: Optional[bool] = None


@router.patch("/papers/{paper_id}")
@api
async def edit_paper(paper_id: str, body: PaperPatch):
    """Correct a paper's tagging (subject, years, paper numbers) or hide it."""
    row = body.model_dump(exclude_none=True)
    row["updated_at"] = "now"
    out = await run(lambda: db().table("reg_past_papers").update(row).eq("id", paper_id).execute().data)
    papers_service._catalogue_cache.clear()
    return {"paper": (out or [None])[0]}


class QuestionPatch(BaseModel):
    text: Optional[str] = None
    marks: Optional[float] = None
    marking_scheme: Optional[str] = None
    topic_id: Optional[str] = None


@router.patch("/questions/{question_id}")
@api
async def edit_question(question_id: str, body: QuestionPatch):
    """Fix a question, its marks, marking scheme or topic tag."""
    out = await run(lambda: db().table("reg_past_questions").update(body.model_dump(exclude_none=True))
                    .eq("id", question_id).execute().data)
    return {"question": (out or [None])[0]}


# ==========================================================================
# Syllabus (subject -> topic -> sub-topic)
# ==========================================================================
@router.get("/syllabus/{subject}")
@api
async def syllabus(subject: str):
    s = topics.subject_by_any(subject)
    if not s:
        raise LookupError("Unknown subject")
    return {"subject": s, "topics": await run(topics.topic_tree, s["id"])}


class TopicIn(BaseModel):
    subject: str
    name: str
    parent_id: Optional[str] = None
    code: Optional[str] = None
    sort: int = 0


@router.post("/syllabus/topics", status_code=201)
@api
async def add_topic(body: TopicIn):
    s = topics.subject_by_any(body.subject)
    if not s:
        raise ValueError("Unknown subject")
    tid = await run(topics.get_or_create_topic, s["id"], body.name, body.parent_id)
    out = await run(lambda: db().table("reg_syllabus_topics").update(
        {"code": body.code, "sort": body.sort, "verified": True}).eq("id", tid).execute().data[0])
    return {"topic": out}


class TopicPatch(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    sort: Optional[int] = None
    verified: Optional[bool] = None
    parent_id: Optional[str] = None


@router.patch("/syllabus/topics/{topic_id}")
@api
async def edit_topic(topic_id: str, body: TopicPatch):
    out = await run(lambda: db().table("reg_syllabus_topics").update(body.model_dump(exclude_none=True))
                    .eq("id", topic_id).execute().data)
    return {"topic": (out or [None])[0]}


class MergeIn(BaseModel):
    into_topic_id: str


@router.post("/syllabus/topics/{topic_id}/merge")
@api
async def merge_topic(topic_id: str, body: MergeIn):
    """Fold an auto-created topic into the official one; history moves with it."""
    def go():
        for table, col in (("reg_messages", "topic_id"), ("reg_past_questions", "topic_id"),
                           ("reg_track_steps", "topic_id"), ("reg_answer_cache", "topic_id")):
            db().table(table).update({col: body.into_topic_id}).eq(col, topic_id).execute()
        for st in db().table("reg_topic_stats").select("*").eq("topic_id", topic_id).execute().data or []:
            db().rpc("reg_bump_topic_stat", {
                "p_user": st["user_id"], "p_topic": body.into_topic_id, "p_subject": st["subject_id"],
                "p_asked": st["asked"], "p_attempted": st["attempted"], "p_awarded": st["marks_awarded"],
                "p_total": st["marks_total"], "p_unsure": st["unsure_count"]}).execute()
        db().table("reg_topic_stats").delete().eq("topic_id", topic_id).execute()
        db().table("reg_syllabus_topics").update({"parent_id": body.into_topic_id}).eq("parent_id", topic_id).execute()
        db().table("reg_syllabus_topics").delete().eq("id", topic_id).execute()
    await run(go)
    return {"merged_into": body.into_topic_id}


# ==========================================================================
# Answer quality (LLM-7)
# ==========================================================================
@router.get("/review")
@api
async def review_queue(status: str = "pending"):
    """Thumbs-down answers awaiting review, with the question that prompted them."""
    rows = await run(lambda: db().table("reg_messages")
                     .select("id,conversation_id,user_id,content,created_at,subject_id,topic_id,review_status,correction")
                     .eq("review_status", status).order("created_at", desc=True).limit(200).execute().data or [])
    for r in rows:
        q = await run(lambda: one(db().table("reg_messages").select("content")
                                  .eq("conversation_id", r["conversation_id"]).eq("role", "user")
                                  .lt("created_at", r["created_at"]).order("created_at", desc=True)))
        r["question"] = (q or {}).get("content")
        r.pop("user_id", None)
    return {"items": rows}


class ReviewIn(BaseModel):
    action: str = Field(..., description="correct | dismiss")
    correction: Optional[str] = None


@router.post("/review/{message_id}")
@api
async def resolve_review(message_id: str, body: ReviewIn):
    """Correcting an answer also puts the corrected text into the answer cache,
    so the next student who asks gets the corrected answer."""
    msg = await run(lambda: one(db().table("reg_messages").select("*").eq("id", message_id)))
    if not msg:
        raise LookupError("Message not found")
    if body.action == "dismiss":
        await run(lambda: db().table("reg_messages").update({"review_status": "dismissed"}).eq("id", message_id).execute())
        return {"status": "dismissed"}
    if body.action != "correct" or not (body.correction or "").strip():
        raise ValueError("Give a correction, or dismiss")
    await run(lambda: db().table("reg_messages").update(
        {"review_status": "corrected", "correction": body.correction.strip()}).eq("id", message_id).execute())
    qhash = (msg.get("metadata") or {}).get("question_hash")
    if qhash:
        await run(lambda: db().table("reg_answer_cache").update(
            {"answer": body.correction.strip(), "disabled": False}).eq("question_hash", qhash).execute())
    return {"status": "corrected", "cache_updated": bool(qhash)}


# ==========================================================================
# Teachers (FR-V1, FR-V2)
# ==========================================================================
@router.get("/teachers")
@api
async def teachers(status: str = "pending"):
    rows = await run(lambda: db().table("reg_teacher_profiles").select("*, reg_users(phone_number,role)")
                     .eq("verification_status", status).order("created_at").execute().data or [])
    return {"teachers": rows}


class VerifyTeacherIn(BaseModel):
    status: str = Field(..., description="verified | rejected | pending")
    id_verified: Optional[bool] = None
    credential_verified: Optional[bool] = None
    school_confirmed: Optional[bool] = None
    results_verified: Optional[bool] = Field(None, description="Only true once the claim is evidenced")
    revenue_share: Optional[float] = Field(None, ge=0.65, le=0.75)


@router.post("/teachers/{user_id}/verify")
@api
async def verify_teacher(user_id: str, body: VerifyTeacherIn):
    if body.status not in ("verified", "rejected", "pending"):
        raise ValueError("Unknown status")
    row = body.model_dump(exclude_none=True)
    row["verification_status"] = row.pop("status")
    if row["verification_status"] == "verified":
        p = await run(lambda: one(db().table("reg_teacher_profiles").select("*").eq("user_id", user_id)))
        if not p:
            raise LookupError("No application from this user")
        merged = {**p, **row}
        if not (merged["id_verified"] and merged["credential_verified"] and merged["school_confirmed"]):
            raise ValueError("Verify ID, credential and school before approving")
    row["updated_at"] = "now"
    out = await run(lambda: db().table("reg_teacher_profiles").update(row).eq("user_id", user_id).execute().data)
    if row["verification_status"] == "verified":
        await run(lambda: db().table("reg_users").update({"role": "teacher"}).eq("id", user_id)
                  .eq("role", "student").execute())
    return {"profile": (out or [None])[0]}


# ==========================================================================
# Payments and payouts (FR-M1..M3)
# ==========================================================================
@router.get("/payments")
@api
async def list_payments(status: str = "pending"):
    return {"payments": await run(lambda: db().table("reg_payments").select("*").eq("status", status)
                                  .order("created_at", desc=True).limit(500).execute().data or [])}


class ConfirmIn(BaseModel):
    provider_ref: Optional[str] = Field(None, description="Mobile-money transaction id")


@router.post("/payments/{payment_id}/confirm")
@api
async def confirm_payment(payment_id: str, body: ConfirmIn):
    """Mark a payment received and grant what was bought (idempotent)."""
    return {"result": await run(payments.confirm, payment_id, body.provider_ref)}


@router.post("/payments/{payment_id}/fail")
@api
async def fail_payment(payment_id: str):
    await run(payments.mark_failed, payment_id)
    return {"status": "failed"}


class PayoutIn(BaseModel):
    teacher_id: str
    period_start: str
    period_end: str
    amount_fcfa: int = Field(..., ge=0)


@router.get("/payouts")
@api
async def list_payouts(status: str = ""):
    def load():
        q = db().table("reg_payouts").select("*")
        if status:
            q = q.eq("status", status)
        rows = q.order("created_at", desc=True).limit(500).execute().data or []
        ids = list({r["teacher_id"] for r in rows})
        names = {p["user_id"]: p["display_name"] for p in (db().table("reg_teacher_profiles")
                 .select("user_id,display_name").in_("user_id", ids).execute().data if ids else [])}
        return [{**r, "teacher_name": names.get(r["teacher_id"])} for r in rows]

    return {"payouts": await run(load)}


@router.post("/payouts", status_code=201)
@api
async def create_payout(body: PayoutIn):
    return {"payout": await run(lambda: db().table("reg_payouts").insert(body.model_dump()).execute().data[0])}


class PayoutPaidIn(BaseModel):
    provider_ref: Optional[str] = None


@router.post("/payouts/{payout_id}/paid")
@api
async def payout_paid(payout_id: str, body: PayoutPaidIn):
    return {"payout": await run(lambda: db().table("reg_payouts").update(
        {"status": "paid", "paid_at": "now", "provider_ref": body.provider_ref}).eq("id", payout_id).execute().data[0])}


# ==========================================================================
# Analytics (FR-A3)
# ==========================================================================
@router.get("/stats")
@api
async def stats(days: int = 7):
    """Free-tier usage and conversion for the last `days` days (WAT), with the
    rule-of-thumb flags from the requirements."""
    return await run(_stats, max(1, min(days, 90)))


def _stats(days: int) -> dict:
    end = allowance.wat_now().date()
    start = end - timedelta(days=days - 1)
    prev_start = start - timedelta(days=days)

    def users_active(a: date, b: date) -> set:
        msgs = db().table("reg_messages").select("user_id").eq("role", "user") \
            .gte("created_at", a.isoformat()).lt("created_at", (b + timedelta(days=1)).isoformat()).limit(100000).execute().data or []
        atts = db().table("reg_attempts").select("user_id") \
            .gte("created_at", a.isoformat()).lt("created_at", (b + timedelta(days=1)).isoformat()).limit(100000).execute().data or []
        return {r["user_id"] for r in msgs + atts}

    active = users_active(start, end)
    previous = users_active(prev_start, start - timedelta(days=1))
    usage = db().table("reg_daily_usage").select("user_id,day,questions_used,papers_used") \
        .gte("day", start.isoformat()).lte("day", end.isoformat()).limit(100000).execute().data or []
    q_cap_hitters = {u["user_id"] for u in usage if u["questions_used"] >= config.FREE_QUESTIONS_PER_DAY}
    p_cap_hitters = {u["user_id"] for u in usage if u["papers_used"] >= config.FREE_PAPERS_PER_DAY}
    paid = db().table("reg_payments").select("user_id,type,amount_fcfa").eq("status", "paid") \
        .gte("paid_at", start.isoformat()).limit(100000).execute().data or []
    topup_buyers = {p["user_id"] for p in paid if p["type"] != "track"}
    track_buyers = {p["user_id"] for p in paid if p["type"] == "track"}
    answers = db().table("reg_messages").select("id", count="exact").eq("role", "assistant") \
        .eq("is_reexplanation", False).gte("created_at", start.isoformat()).execute().count or 0
    rated = db().table("reg_messages").select("rating").not_.is_("rating", "null") \
        .gte("created_at", start.isoformat()).limit(100000).execute().data or []

    def pct(a, b):
        return round(100 * a / b, 1) if b else 0.0

    hitters = q_cap_hitters | p_cap_hitters
    topup_rev = sum(p["amount_fcfa"] for p in paid if p["type"] != "track")
    track_rev = sum(p["amount_fcfa"] for p in paid if p["type"] == "track")
    out = {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "active_students": len(active),
        "pct_hit_question_cap": pct(len(q_cap_hitters & active), len(active)),
        "pct_hit_paper_cap": pct(len(p_cap_hitters & active), len(active)),
        "topup_conversion_pct": pct(len(topup_buyers & active), len(active)),
        "track_conversion_pct": pct(len(track_buyers & active), len(active)),
        "track_conversion_cap_hitters_pct": pct(len(track_buyers & hitters), len(hitters)),
        "track_conversion_others_pct": pct(len(track_buyers & (active - hitters)), len(active - hitters)),
        "retention_pct": pct(len(active & previous), len(previous)),
        "answers_per_active_student": round(answers / len(active), 2) if active else 0,
        "ratings": {"up": sum(1 for r in rated if r["rating"] == 1), "down": sum(1 for r in rated if r["rating"] == -1)},
        "revenue_fcfa": {"topups": topup_rev, "tracks": track_rev},
    }
    flags = []
    if active and out["pct_hit_question_cap"] < 10:
        flags.append("Fewer than 10% of active students hit the question cap: consider raising it.")
    if topup_rev > track_rev:
        flags.append("Top-ups outsold tracks: the track price is probably wrong.")
    out["flags"] = flags
    return out
