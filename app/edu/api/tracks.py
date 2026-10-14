"""
Teacher tracks API (prefix /api/edu).

  /tracks/...   students and parents: catalogue, enrol, learn, Q&A, refund
  /teacher/...  teachers: apply, build tracks, cohort, digest, marking, earnings
"""
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.edu import papers_service, topics
from app.edu import tracks_service as T
from app.edu.api.common import api
from app.edu.auth import get_current_user
from app.edu.db import run

router = APIRouter(prefix="/api/edu", tags=["Carati — tracks"])


# ==========================================================================
# Students and parents
# ==========================================================================
@router.get("/tracks")
@api
async def catalogue(subject: str = "", user: dict = Depends(get_current_user)):
    """Published tracks: teacher, school, experience, evidenced results, start
    date, price (FCFA), number enrolled (FR-C1)."""
    s = topics.subject_by_any(subject) if subject else None
    return {"tracks": await run(T.catalogue, s["id"] if s else None)}


@router.get("/tracks/recommended")
@api
async def recommended(user: dict = Depends(get_current_user)):
    """Tracks for subjects where the student is weak (FR-C2)."""
    return {"tracks": await run(T.recommended, user["id"])}


@router.get("/tracks/mine")
@api
async def my_tracks(user: dict = Depends(get_current_user)):
    from app.edu.db import db
    rows = await run(lambda: db().table("reg_enrolments").select("track_id,status")
                     .eq("student_id", user["id"]).in_("status", ["active", "pending"]).execute().data or [])
    out = []
    for r in rows:
        if r["status"] == "active":
            v = await run(T.student_track, user, r["track_id"])
            out.append({"status": "active", **v})
        else:
            t = await run(T.track_detail, r["track_id"])
            out.append({"status": "pending", "track": {k: t[k] for k in ("id", "title", "subject_id", "exam_year")}})
    return {"enrolments": out}


@router.get("/tracks/{track_id}")
@api
async def track_overview(track_id: str, user: dict = Depends(get_current_user)):
    """Public outline: step titles and dates only (content is for enrolled students)."""
    listing = next((t for t in await run(T.catalogue) if t["id"] == track_id), None)
    if not listing:
        raise LookupError("Track not found")
    t = await run(T.track_detail, track_id)
    return {**listing, "steps": [{"week_number": s["week_number"], "title": s["title"], "topic": s["topic"],
                                  "unlock_date": s["unlock_date"]} for s in t["steps"]],
            "enrolled": bool(await run(T.my_enrolment, user["id"], track_id))}


class EnrolIn(BaseModel):
    parent_phone: str = Field(..., description="Parent's mobile-money number: receives the payment push and receipt")


@router.post("/tracks/{track_id}/enrol")
@api
async def enrol(track_id: str, body: EnrolIn, user: dict = Depends(get_current_user)):
    """Start buying a track. Access starts as soon as the payment is confirmed."""
    return await run(T.start_enrolment, user, track_id, body.parent_phone)


@router.get("/tracks/{track_id}/learn")
@api
async def learn(track_id: str, user: dict = Depends(get_current_user)):
    return await run(T.student_track, user, track_id)


@router.get("/tracks/{track_id}/steps/{step_id}")
@api
async def step(track_id: str, step_id: str, user: dict = Depends(get_current_user)):
    return await run(T.student_step, user, track_id, step_id)


@router.post("/tracks/{track_id}/steps/{step_id}/complete")
@api
async def complete(track_id: str, step_id: str, user: dict = Depends(get_current_user)):
    return {"pace": await run(T.complete_step, user, track_id, step_id)}


@router.get("/tracks/{track_id}/items/{item_id}/file")
@api
async def item_file(track_id: str, item_id: str, user: dict = Depends(get_current_user)):
    """Streamed in-app only; PDFs carry the student's phone number on every page."""
    data, mime = await run(T.item_file, user, track_id, item_id)
    return Response(content=data, media_type=mime,
                    headers={"Content-Disposition": "inline", "Cache-Control": "private, no-store"})


@router.get("/tracks/{track_id}/items/{item_id}/pages")
@api
async def item_page_count(track_id: str, item_id: str, user: dict = Depends(get_current_user)):
    """Number of pages of a document item (render with the /pages/{n} images)."""
    return await run(T.item_pages, user, track_id, item_id)


@router.get("/tracks/{track_id}/items/{item_id}/pages/{page}")
@api
async def item_page(track_id: str, item_id: str, page: int, user: dict = Depends(get_current_user)):
    """One page of a document item as a PNG, watermarked with the student's phone."""
    png = await run(T.item_pages, user, track_id, item_id, page)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, no-store"})


@router.get("/tracks/{track_id}/posts")
@api
async def track_posts(track_id: str, week: str = "", user: dict = Depends(get_current_user)):
    if not await run(T.my_enrolment, user["id"], track_id):
        raise PermissionError("Not enrolled in this track")
    return {"posts": await run(T.posts, track_id, week or None)}


class AskIn(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)


@router.post("/tracks/{track_id}/posts")
@api
async def ask_track(track_id: str, body: AskIn, user: dict = Depends(get_current_user)):
    """Ask in the track's weekly Q&A. Carati answers first; the teacher sees it in the digest."""
    return {"posts": await T.ask_in_track(user, track_id, body.question)}


@router.post("/tracks/{track_id}/refund")
@api
async def refund(track_id: str, user: dict = Depends(get_current_user)):
    """Full refund within 7 days if fewer than two steps are done (FR-M3)."""
    return await run(T.request_refund, user, track_id)


# ==========================================================================
# Teachers
# ==========================================================================
class TeacherApplyIn(BaseModel):
    display_name: str
    school: str
    years_experience: int = Field(..., ge=0, le=60)
    bio: Optional[str] = None
    results_claim: Optional[str] = Field(None, description="Shown only once evidenced and verified")
    results_evidence_url: Optional[str] = None
    id_document_url: Optional[str] = Field(None, description="National ID scan (FR-V1)")
    credential_url: Optional[str] = Field(None, description="Teaching credential (FR-V1)")
    payout_provider: Optional[str] = Field(None, description="mtn_momo | orange_money")
    payout_wallet: Optional[str] = None
    accept_agreement: bool = Field(..., description="Revenue-share agreement (FR-V3)")


@router.post("/teacher/apply")
@api
async def apply(body: TeacherApplyIn, user: dict = Depends(get_current_user)):
    data = body.model_dump()
    return {"profile": await run(T.apply_as_teacher, user, data, data.pop("accept_agreement"))}


@router.get("/teacher/profile")
@api
async def teacher_profile(user: dict = Depends(get_current_user)):
    return {"profile": await run(T.teacher_profile, user["id"])}


async def _teacher(user: dict = Depends(get_current_user)) -> dict:
    """Dependencies run outside @api, so map the refusal to a 403 here."""
    from fastapi import HTTPException
    try:
        await run(T.require_verified_teacher, user)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return user


class TrackIn(BaseModel):
    title: str
    subject_id: str
    exam_year: int
    description: Optional[str] = None
    variant: str = Field("season", description="season | sprint (shorter pre-exam variant)")
    price_fcfa: int = Field(..., ge=0)
    start_date: str = Field(..., description="YYYY-MM-DD")
    exam_date: str = Field(..., description="YYYY-MM-DD")


class TrackPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    variant: Optional[str] = None
    price_fcfa: Optional[int] = Field(None, ge=0)
    start_date: Optional[str] = None
    exam_date: Optional[str] = None
    exam_year: Optional[int] = None


@router.get("/teacher/tracks")
@api
async def my_teacher_tracks(user: dict = Depends(_teacher)):
    from app.edu.db import db

    def load():
        tracks = db().table("reg_tracks").select("*").eq("teacher_id", user["id"])             .order("created_at", desc=True).execute().data or []
        ids = [t["id"] for t in tracks]
        counts: dict = {}
        if ids:
            for e in db().table("reg_enrolments").select("track_id").in_("track_id", ids)                     .eq("status", "active").execute().data or []:
                counts[e["track_id"]] = counts.get(e["track_id"], 0) + 1
        return [{**t, "enrolled": counts.get(t["id"], 0)} for t in tracks]

    return {"tracks": await run(load)}


@router.post("/teacher/tracks", status_code=201)
@api
async def create_track(body: TrackIn, user: dict = Depends(_teacher)):
    return {"track": await run(T.create_track, user, body.model_dump())}


@router.get("/teacher/tracks/{track_id}")
@api
async def get_teacher_track(track_id: str, user: dict = Depends(_teacher)):
    await run(T.own_track, user["id"], track_id)
    return {"track": await run(T.track_detail, track_id)}


@router.patch("/teacher/tracks/{track_id}")
@api
async def patch_track(track_id: str, body: TrackPatch, user: dict = Depends(_teacher)):
    return {"track": await run(T.update_track, user, track_id, body.model_dump(exclude_none=True))}


class StatusIn(BaseModel):
    status: str = Field(..., description="draft | published | archived")


@router.post("/teacher/tracks/{track_id}/status")
@api
async def track_status(track_id: str, body: StatusIn, user: dict = Depends(_teacher)):
    if body.status not in ("draft", "published", "archived"):
        raise ValueError("Unknown status")
    return {"track": await run(T.set_status, user, track_id, body.status)}


class CloneIn(BaseModel):
    exam_year: Optional[int] = None


@router.post("/teacher/tracks/{track_id}/clone", status_code=201)
@api
async def clone(track_id: str, body: CloneIn, user: dict = Depends(_teacher)):
    """Copy a track to the next exam year in one click."""
    return {"track": await run(T.clone_track, user, track_id, body.exam_year)}


class StepIn(BaseModel):
    week_number: int = Field(..., ge=1)
    title: str
    topic: Optional[str] = None
    topic_id: Optional[str] = None
    notes: Optional[str] = None
    checkpoint: Optional[str] = None


class StepPatch(BaseModel):
    week_number: Optional[int] = Field(None, ge=1)
    title: Optional[str] = None
    topic: Optional[str] = None
    topic_id: Optional[str] = None
    notes: Optional[str] = None
    checkpoint: Optional[str] = None


@router.post("/teacher/tracks/{track_id}/steps", status_code=201)
@api
async def add_step(track_id: str, body: StepIn, user: dict = Depends(_teacher)):
    return {"step": await run(T.upsert_step, user, track_id, body.model_dump())}


@router.patch("/teacher/tracks/{track_id}/steps/{step_id}")
@api
async def edit_step(track_id: str, step_id: str, body: StepPatch, user: dict = Depends(_teacher)):
    return {"step": await run(T.upsert_step, user, track_id, body.model_dump(exclude_none=True), step_id)}


@router.delete("/teacher/tracks/{track_id}/steps/{step_id}")
@api
async def remove_step(track_id: str, step_id: str, user: dict = Depends(_teacher)):
    await run(T.delete_step, user, track_id, step_id)
    return {"deleted": True}


@router.post("/teacher/tracks/{track_id}/steps/{step_id}/items", status_code=201)
@api
async def add_item(
    track_id: str, step_id: str,
    kind: str = Form(..., description="note | document | audio | worked_question | question"),
    title: str = Form(""),
    body: str = Form(""),
    past_question_id: str = Form(""),
    sort: int = Form(0),
    file: UploadFile = File(None),
    user: dict = Depends(_teacher),
):
    if kind not in ("note", "document", "audio", "worked_question", "question"):
        raise ValueError("Unknown item kind")
    if kind in ("document", "audio") and (file is None or not file.filename):
        raise ValueError("Attach the file")
    if kind in ("worked_question", "question") and not past_question_id:
        raise ValueError("Pick a past question")
    data = {"kind": kind, "title": title or None, "body": body or None,
            "past_question_id": past_question_id or None, "sort": sort}
    content = await file.read() if file is not None and file.filename else None
    return {"item": await run(T.add_item, user, track_id, step_id, data, content,
                              file.filename if content is not None else None,
                              file.content_type if content is not None else None)}


@router.delete("/teacher/tracks/{track_id}/items/{item_id}")
@api
async def remove_item(track_id: str, item_id: str, user: dict = Depends(_teacher)):
    await run(T.delete_item, user, track_id, item_id)
    return {"deleted": True}


@router.get("/teacher/papers/{paper_id}/questions")
@api
async def teacher_paper_questions(paper_id: str, user: dict = Depends(_teacher)):
    """Questions of a past paper, to attach to steps (no allowance for teachers)."""
    return {"questions": await papers_service.ensure_questions(paper_id)}


@router.get("/teacher/tracks/{track_id}/cohort")
@api
async def cohort(track_id: str, user: dict = Depends(_teacher)):
    """Each student's heat map, pace and last-week activity; falling-behind list."""
    return await run(T.cohort, user, track_id)


@router.get("/teacher/tracks/{track_id}/digest")
@api
async def digest(track_id: str, week: str = "", user: dict = Depends(_teacher)):
    """What the cohort asked this week (track Q&A + chat topics)."""
    return await run(T.weekly_digest, user, track_id, week or None)


class PostIn(BaseModel):
    body: str = Field(..., min_length=3, max_length=4000)
    kind: str = Field("clarification", description="clarification (one per week) | announcement")


@router.post("/teacher/tracks/{track_id}/posts", status_code=201)
@api
async def teacher_post(track_id: str, body: PostIn, user: dict = Depends(_teacher)):
    if body.kind not in ("clarification", "announcement"):
        raise ValueError("kind must be clarification or announcement")
    return {"post": await run(T.post_clarification, user, track_id, body.body, body.kind)}


@router.get("/teacher/marking")
@api
async def marking_queue(user: dict = Depends(_teacher)):
    """Attempts students asked the teacher to mark (capped per student per week)."""
    return {"attempts": await run(T.marking_queue, user)}


class MarkIn(BaseModel):
    marks_awarded: float = Field(..., ge=0)
    feedback: str


@router.post("/teacher/marking/{attempt_id}")
@api
async def mark(attempt_id: str, body: MarkIn, user: dict = Depends(_teacher)):
    return {"attempt": await run(T.teacher_mark, user, attempt_id, body.marks_awarded, body.feedback)}


@router.get("/teacher/earnings")
@api
async def earnings(user: dict = Depends(_teacher)):
    return await run(T.earnings, user)
