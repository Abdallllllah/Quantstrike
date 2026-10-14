"""
Past paper bank (FR-S2).

* Papers are catalogued in reg_past_papers from the documents already in the
  library (recognised by filename, see app/edu/papers.py). sync_papers() is
  safe to run any time; it runs automatically the first time the bank is empty.
* Opening a paper in full spends one paper from the daily allowance. Re-opening
  one already unlocked today, or one the student has completed, is free.
* Questions are pulled out of a paper's text on first use and stored, so each
  can be shown and attempted one at a time.
* Attempts are marked examiner-style (method/accuracy marks) by the cheaper
  marking model (LLM-3, LLM-6) and feed the student profile.
"""
import asyncio
import time
from typing import Optional

from app.edu import allowance, config, llm, papers, topics
from app.edu.db import db, general_school_id, one, rpc, run
from app.edu.retrieval import _stitch

PAPER_BUCKET = "reg_documents"


class PaperCapReached(Exception):
    pass


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------
def sync_papers() -> dict:
    """Upsert one reg_past_papers row per past-paper document in the library."""
    school = general_school_id()
    docs = (db().table("reg_documents").select("id,filename,doc_type,subject_id,chunk_count")
            .eq("school_id", school).limit(5000).execute().data) or []
    subject_names = {s["id"]: s["name"] for s in (db().table("reg_subjects").select("id,name").execute().data or [])}

    best: dict = {}
    for d in docs:
        cur = best.get(d["filename"])
        if cur is None or (d.get("chunk_count") or 0) > (cur.get("chunk_count") or 0):
            best[d["filename"]] = d

    existing = {r["document_id"]: r for r in
                (db().table("reg_past_papers").select("id,document_id").limit(5000).execute().data or [])}
    added = updated = 0
    for d in best.values():
        rec = papers.classify(d, subject_names.get(d.get("subject_id")))
        if not rec:
            continue
        row = {
            "document_id": d["id"], "subject_id": d.get("subject_id"), "filename": d["filename"],
            "title": rec["title"], "years": rec["years"], "paper_numbers": rec["papers"],
            "kind": rec["kind"], "is_active": True,
        }
        if d["id"] in existing:
            db().table("reg_past_papers").update(row).eq("id", existing[d["id"]]["id"]).execute()
            updated += 1
        else:
            db().table("reg_past_papers").insert(row).execute()
            added += 1
    _catalogue_cache.clear()
    return {"added": added, "updated": updated, "documents_scanned": len(docs)}


_catalogue_cache: dict = {}


def _catalogue() -> list:
    """All active papers as search records (cached for a minute)."""
    hit = _catalogue_cache.get("all")
    if hit and time.monotonic() - hit[0] < 60:
        return hit[1]
    rows = (db().table("reg_past_papers")
            .select("id,subject_id,filename,title,years,paper_numbers,kind,questions_extracted_at")
            .eq("is_active", True).limit(5000).execute().data) or []
    if not rows:
        sync_papers()
        rows = (db().table("reg_past_papers")
                .select("id,subject_id,filename,title,years,paper_numbers,kind,questions_extracted_at")
                .eq("is_active", True).limit(5000).execute().data) or []
    names = {s["id"]: s["name"] for s in (db().table("reg_subjects").select("id,name").execute().data or [])}
    records = []
    for r in rows:
        subject = names.get(r["subject_id"])
        records.append({
            "id": r["id"], "title": r["title"], "filename": r["filename"], "subject": subject,
            "subject_id": r["subject_id"], "years": r["years"] or [], "papers": r["paper_numbers"] or [],
            "kind": r["kind"], "has_questions": bool(r["questions_extracted_at"]),
            "_tokens": papers._tokens(r["filename"]) + papers._tokens(subject or ""),
        })
    _catalogue_cache["all"] = (time.monotonic(), records)
    return records


def _papers_with_topic(topic: str) -> set:
    rows = (db().table("reg_past_questions").select("paper_id,reg_syllabus_topics!inner(name)")
            .ilike("reg_syllabus_topics.name", f"%{topic}%").limit(2000).execute().data) or []
    return {r["paper_id"] for r in rows}


def _student_paper_state(user_id: str) -> dict:
    """paper_id -> {unlocked_today, completed, attempted}"""
    today = allowance.wat_now().date().isoformat()
    state: dict = {}
    for u in (db().table("reg_paper_unlocks").select("paper_id,day,completed_at")
              .eq("user_id", user_id).limit(5000).execute().data or []):
        s = state.setdefault(u["paper_id"], {"unlocked_today": False, "completed": False, "unlocked_ever": True})
        s["unlocked_today"] |= u["day"] == today
        s["completed"] |= bool(u["completed_at"])
    return state


def search(user_id: str, q: str = "", subject: str = "", year: Optional[int] = None,
           paper: Optional[int] = None, topic: str = "", limit: int = 50) -> dict:
    records = _catalogue()
    found = papers.search(records, q=q, subject=subject, year=year, paper=paper)
    if topic.strip():
        allowed = _papers_with_topic(topic.strip())
        found = [p for p in found if p["id"] in allowed]
    state = _student_paper_state(user_id)
    out = []
    for p in found[:max(1, min(limit, 200))]:
        s = state.get(p["id"], {})
        out.append({**papers.public(p), "unlocked_today": s.get("unlocked_today", False),
                    "completed": s.get("completed", False), "unlocked_ever": s.get("unlocked_ever", False)})
    return {"count": len(found), "papers": out}


def paper_view(user_id: str, paper_id: str) -> dict:
    """One paper with the student's state (browsing is free)."""
    rec = next((p for p in _catalogue() if p["id"] == paper_id), None)
    if not rec:
        raise LookupError("Paper not found")
    st = _student_paper_state(user_id).get(paper_id, {})
    return {**papers.public(rec), "unlocked_today": st.get("unlocked_today", False),
            "completed": st.get("completed", False), "unlocked_ever": st.get("unlocked_ever", False)}


def get_paper(paper_id: str) -> dict:
    row = one(db().table("reg_past_papers").select("*").eq("id", paper_id).eq("is_active", True))
    if not row:
        raise LookupError("Paper not found")
    return row


# --------------------------------------------------------------------------
# Opening a paper (the allowance)
# --------------------------------------------------------------------------
def open_paper(user_id: str, paper_id: str) -> dict:
    """Unlock a paper for today. Free if already unlocked today or completed
    before; otherwise spends a free paper, then a season pass, then a credit."""
    paper = get_paper(paper_id)
    today = allowance.wat_now().date().isoformat()
    unlocks = (db().table("reg_paper_unlocks").select("day,completed_at")
               .eq("user_id", user_id).eq("paper_id", paper_id).execute().data) or []
    if any(u["day"] == today for u in unlocks):
        return {"paper_id": paper_id, "source": "reopen", "charged": False}
    if any(u["completed_at"] for u in unlocks):
        source, charged = "completed", False
    else:
        spent = allowance.consume(user_id, "paper", f"paper:{paper_id}", paper.get("subject_id"))
        if not spent["allowed"]:
            raise PaperCapReached()
        source, charged = spent["source"], not spent["already"]
    db().table("reg_paper_unlocks").upsert(
        {"user_id": user_id, "paper_id": paper_id, "day": today, "source": source},
        on_conflict="user_id,paper_id,day", ignore_duplicates=True).execute()
    return {"paper_id": paper_id, "source": source, "charged": charged}


def has_access(user_id: str, paper_id: str) -> bool:
    """Unlocked on some day (so attempts and review keep working), or reachable
    through an active track."""
    if one(db().table("reg_paper_unlocks").select("id").eq("user_id", user_id).eq("paper_id", paper_id)):
        return True
    return False


def can_download(user_id: str, paper_id: str) -> bool:
    today = allowance.wat_now().date().isoformat()
    rows = (db().table("reg_paper_unlocks").select("day,completed_at")
            .eq("user_id", user_id).eq("paper_id", paper_id).execute().data) or []
    return any(r["day"] == today or r["completed_at"] for r in rows)


def download(paper_id: str) -> tuple:
    paper = get_paper(paper_id)
    data = db().storage.from_(PAPER_BUCKET).download(paper["filename"])
    return data, paper["filename"]


# --------------------------------------------------------------------------
# Questions (extracted once, then served one at a time)
# --------------------------------------------------------------------------
_EXTRACT_PROMPT = """You are digitising a Cameroon GCE Advanced Level past paper.
From the paper text, list every question in order. Keep sub-parts with their question
("2(a)", "2(b)") as separate items when they carry their own marks. For multiple-choice
papers, each item is one MCQ with its options A-D included in the text.
If the document includes a marking scheme or answers, copy the relevant part for each question.

Return JSON only:
{"questions": [{"number": "1", "text": "full question text incl. data and options",
                "marks": 4 or null, "marking_scheme": "text or null",
                "topic": "short syllabus topic name"}]}
Plain text in every field: no markdown, no LaTeX (use unicode for maths)."""

_extract_locks: dict = {}


def _paper_text(document_id: str) -> str:
    rows = (db().table("reg_embeddings").select("content,page_number,chunk_index,metadata")
            .eq("document_id", document_id).order("chunk_index").limit(5000).execute().data) or []
    return _stitch(rows)


async def ensure_questions(paper_id: str) -> list:
    paper = await run(get_paper, paper_id)
    if not paper.get("questions_extracted_at"):
        lock = _extract_locks.setdefault(paper_id, asyncio.Lock())
        async with lock:
            paper = await run(get_paper, paper_id)
            if not paper.get("questions_extracted_at"):
                await _extract(paper)
    return await run(lambda: db().table("reg_past_questions")
                     .select("id,sort,number,text,marks,topic_id").eq("paper_id", paper_id)
                     .order("sort").execute().data or [])


async def _extract(paper: dict) -> None:
    text = await run(_paper_text, paper["document_id"]) if paper.get("document_id") else ""
    if not text.strip():
        raise LookupError("This paper has no readable text yet")
    data = await llm.complete_json([
        {"role": "system", "content": _EXTRACT_PROMPT},
        {"role": "user", "content": text[:120000]},
    ], model=config.MARKING_MODEL, max_tokens=16000, timeout=180)
    items = (data or {}).get("questions") if isinstance(data, dict) else None
    if not items:
        raise RuntimeError("Could not read the questions from this paper")

    def save():
        db().table("reg_past_questions").delete().eq("paper_id", paper["id"]).execute()
        rows = []
        for i, q in enumerate(items, start=1):
            qtext = (q.get("text") or "").strip()
            if not qtext:
                continue
            topic_id = (topics.get_or_create_topic(paper["subject_id"], q.get("topic") or "")
                        if paper.get("subject_id") else None)
            marks = q.get("marks")
            rows.append({"paper_id": paper["id"], "sort": i, "number": str(q.get("number") or i)[:20],
                         "text": qtext, "marks": marks if isinstance(marks, (int, float)) else None,
                         "marking_scheme": (q.get("marking_scheme") or None), "topic_id": topic_id})
        if rows:
            db().table("reg_past_questions").insert(rows).execute()
        db().table("reg_past_papers").update({"questions_extracted_at": "now"}).eq("id", paper["id"]).execute()
        _catalogue_cache.clear()

    await run(save)


def get_question(question_id: str) -> dict:
    row = one(db().table("reg_past_questions").select("*, reg_past_papers(id,subject_id,title)").eq("id", question_id))
    if not row:
        raise LookupError("Question not found")
    return row


# --------------------------------------------------------------------------
# Attempts and marking (LLM-3 examiner style)
# --------------------------------------------------------------------------
_MARK_PROMPT = """You are an experienced Cameroon GCE Advanced Level examiner. Mark the student's answer
strictly by GCE marking-scheme conventions: method marks (M) for a correct method even with
slips, accuracy marks (A) only for correct results that follow from correct method, B marks
for independent correct statements. Follow through errors fairly. Use GCE terminology.

Question ({marks} marks):
{question}

Marking scheme (may be empty; if empty, mark against the standard GCE answer):
{scheme}

Return JSON only:
{{"marks_awarded": number, "marks_total": number,
  "breakdown": [{{"point": "what earns the mark", "type": "M|A|B", "awarded": number, "max": number, "comment": "short"}}],
  "feedback": "2-4 sentences, examiner style: what earned marks, the first error, how to fix it"}}
Plain text only, unicode maths, no markdown."""


def _attempt_count_this_week(user_id: str, track_id: str) -> int:
    from datetime import timedelta
    start = allowance.wat_now().date()
    start = (start - timedelta(days=start.weekday())).isoformat()
    res = (db().table("reg_attempts").select("id", count="exact").eq("user_id", user_id)
           .eq("track_id", track_id).in_("marked_by", ["teacher", "pending_teacher"])
           .gte("created_at", start).execute())
    return res.count or 0


async def attempt(user: dict, question_id: str, answer_text: str, image_url: Optional[str] = None,
                  track_id: Optional[str] = None, want_teacher: bool = False) -> dict:
    uid = user["id"]
    question = await run(get_question, question_id)
    paper = question.get("reg_past_papers") or {}
    in_track = bool(track_id) and await run(_question_in_active_track, uid, track_id, question_id)
    if not in_track and not await run(has_access, uid, paper.get("id")):
        raise PermissionError("Open the paper first")

    # FR-T3: teacher marks are rationed per week; beyond the cap the LLM marks.
    if in_track and want_teacher and await run(_attempt_count_this_week, uid, track_id) < config.TEACHER_MARKED_PER_WEEK:
        row = await run(lambda: db().table("reg_attempts").insert({
            "user_id": uid, "question_id": question_id, "track_id": track_id,
            "submission": answer_text, "had_image": bool(image_url),
            "marks_total": question.get("marks"), "marked_by": "pending_teacher",
        }).execute().data[0])
        return {**row, "status": "waiting_for_teacher"}

    marks = question.get("marks") or "?"
    content = [{"type": "text", "text": "Student's answer:\n" + (answer_text or "(see photo)")}]
    if image_url:
        content.append(llm.image_part(image_url))
    data = await llm.complete_json([
        {"role": "system", "content": _MARK_PROMPT.format(
            marks=marks, question=question["text"], scheme=question.get("marking_scheme") or "")},
        {"role": "user", "content": content},
    ], model=config.MARKING_MODEL, max_tokens=1500, timeout=60) or {}
    total = _num(data.get("marks_total")) or _num(question.get("marks")) or 0
    awarded = max(0.0, min(_num(data.get("marks_awarded")) or 0, total or 1e9))
    row = await run(lambda: db().table("reg_attempts").insert({
        "user_id": uid, "question_id": question_id, "track_id": track_id if in_track else None,
        "submission": answer_text, "had_image": bool(image_url),
        "marks_awarded": awarded, "marks_total": total,
        "feedback": (data.get("feedback") or "").strip() or "Marked.",
        "breakdown": data.get("breakdown") if isinstance(data.get("breakdown"), list) else [],
        "marked_by": "llm", "marked_at": "now",
    }).execute().data[0])
    await run(_record_attempt_stats, uid, question, paper, awarded, total)
    return {**row, "status": "marked"}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _record_attempt_stats(user_id: str, question: dict, paper: dict, awarded: float, total: float) -> None:
    if question.get("topic_id") and paper.get("subject_id"):
        rpc("reg_bump_topic_stat", {"p_user": user_id, "p_topic": question["topic_id"],
                                    "p_subject": paper["subject_id"], "p_attempted": 1,
                                    "p_awarded": awarded, "p_total": total})
    _maybe_complete(user_id, paper.get("id"))


def _maybe_complete(user_id: str, paper_id: Optional[str]) -> None:
    """A paper is completed once every question has at least one attempt."""
    if not paper_id:
        return
    qids = {r["id"] for r in (db().table("reg_past_questions").select("id").eq("paper_id", paper_id).execute().data or [])}
    if not qids:
        return
    done = {r["question_id"] for r in (db().table("reg_attempts").select("question_id")
                                       .eq("user_id", user_id).in_("question_id", list(qids)).execute().data or [])}
    if qids <= done:
        db().table("reg_paper_unlocks").update({"completed_at": "now"}).eq("user_id", user_id) \
            .eq("paper_id", paper_id).is_("completed_at", "null").execute()


def _question_in_active_track(user_id: str, track_id: str, question_id: str) -> bool:
    enrol = one(db().table("reg_enrolments").select("id").eq("track_id", track_id)
                .eq("student_id", user_id).eq("status", "active"))
    if not enrol:
        return False
    steps = [s["id"] for s in (db().table("reg_track_steps").select("id").eq("track_id", track_id).execute().data or [])]
    if not steps:
        return False
    return bool(one(db().table("reg_track_items").select("id").in_("step_id", steps)
                    .eq("past_question_id", question_id)))


def attempts_for_paper(user_id: str, paper_id: str) -> list:
    qids = [r["id"] for r in (db().table("reg_past_questions").select("id").eq("paper_id", paper_id).execute().data or [])]
    if not qids:
        return []
    return (db().table("reg_attempts").select("*").eq("user_id", user_id).in_("question_id", qids)
            .order("created_at", desc=True).execute().data) or []
