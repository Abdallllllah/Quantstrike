"""
Teacher revision tracks (FR-T1..T4, FR-C1/C2, FR-V1..V3, FR-M3).

A track is a dated sequence of weekly steps from start_date to the exam, for
one subject. Step N unlocks on start_date + 7·(N-1) days; a student may run
one step ahead of the schedule. Pace: "behind" when the student has finished
fewer than (unlocked steps - 1).

Track content is only ever served in-app, per step; documents are stamped
with the student's phone number on every page (FR-T2).
"""
from datetime import date, timedelta
from typing import Optional

from app.edu import allowance, config, llm, profile
from app.edu.db import db, one
from app.edu.phone import mask_phone

TRACK_BUCKET = "track_content"
RUN_AHEAD_STEPS = 1


def today() -> date:
    return allowance.wat_now().date()


def week_start(d: Optional[date] = None) -> date:
    d = d or today()
    return d - timedelta(days=d.weekday())


# --------------------------------------------------------------------------
# Teacher onboarding and vetting (FR-V1..V3)
# --------------------------------------------------------------------------
PROFILE_FIELDS = ("display_name", "school", "years_experience", "bio", "results_claim",
                  "results_evidence_url", "id_document_url", "credential_url",
                  "payout_provider", "payout_wallet")


def apply_as_teacher(user: dict, data: dict, accept_agreement: bool) -> dict:
    if not accept_agreement:
        raise ValueError("The revenue-share agreement must be accepted")
    row = {k: data.get(k) for k in PROFILE_FIELDS if data.get(k) is not None}
    if not row.get("display_name"):
        raise ValueError("display_name is required")
    row.update({"user_id": user["id"], "agreement_accepted_at": "now"})
    existing = teacher_profile(user["id"])
    if existing:
        row.pop("user_id")
        # Editing evidence sends a verified teacher back for review only if the claim changed.
        if existing.get("results_claim") != row.get("results_claim", existing.get("results_claim")):
            row["results_verified"] = False
        db().table("reg_teacher_profiles").update(row).eq("user_id", user["id"]).execute()
    else:
        db().table("reg_teacher_profiles").insert(row).execute()
    return teacher_profile(user["id"])


def teacher_profile(user_id: str) -> Optional[dict]:
    return one(db().table("reg_teacher_profiles").select("*").eq("user_id", user_id))


def require_verified_teacher(user: dict) -> dict:
    p = teacher_profile(user["id"])
    if not p or p["verification_status"] != "verified" or user.get("role") not in ("teacher", "admin"):
        raise PermissionError("Only verified Carati teachers can do this")
    return p


def public_teacher(p: dict) -> dict:
    """What students and parents see. Results claims only when evidenced (FR-V2)."""
    return {
        "name": p.get("display_name"), "school": p.get("school"),
        "years_experience": p.get("years_experience"), "bio": p.get("bio"),
        "headline": "Experienced GCE teacher",
        "results_claim": p.get("results_claim") if p.get("results_verified") else None,
    }


# --------------------------------------------------------------------------
# Track builder (FR-T1, FR-T4)
# --------------------------------------------------------------------------
TRACK_FIELDS = ("title", "description", "variant", "price_fcfa", "start_date", "exam_date", "exam_year", "subject_id")


def own_track(teacher_id: str, track_id: str) -> dict:
    t = one(db().table("reg_tracks").select("*").eq("id", track_id).eq("teacher_id", teacher_id))
    if not t:
        raise LookupError("Track not found")
    return t


def create_track(teacher: dict, data: dict) -> dict:
    row = {k: data.get(k) for k in TRACK_FIELDS if data.get(k) is not None}
    row["teacher_id"] = teacher["id"]
    return db().table("reg_tracks").insert(row).execute().data[0]


def update_track(teacher: dict, track_id: str, data: dict) -> dict:
    own_track(teacher["id"], track_id)
    row = {k: data[k] for k in TRACK_FIELDS if k in data}
    row["updated_at"] = "now"
    return db().table("reg_tracks").update(row).eq("id", track_id).execute().data[0]


def set_status(teacher: dict, track_id: str, status: str) -> dict:
    t = own_track(teacher["id"], track_id)
    if status == "published":
        if not one(db().table("reg_track_steps").select("id").eq("track_id", track_id)):
            raise ValueError("Add at least one step before publishing")
    return db().table("reg_tracks").update({"status": status, "updated_at": "now"}).eq("id", t["id"]).execute().data[0]


def track_detail(track_id: str) -> dict:
    t = one(db().table("reg_tracks").select("*").eq("id", track_id))
    if not t:
        raise LookupError("Track not found")
    steps = (db().table("reg_track_steps").select("*").eq("track_id", track_id)
             .order("week_number").execute().data) or []
    step_ids = [s["id"] for s in steps]
    items = (db().table("reg_track_items").select("*").in_("step_id", step_ids).order("sort").execute().data
             if step_ids else []) or []
    for s in steps:
        s["items"] = [i for i in items if i["step_id"] == s["id"]]
        s["unlock_date"] = unlock_date(t, s["week_number"]).isoformat()
    t["steps"] = steps
    return t


def unlock_date(track: dict, week_number: int) -> date:
    return date.fromisoformat(track["start_date"]) + timedelta(days=7 * (week_number - 1))


STEP_FIELDS = ("week_number", "title", "topic", "topic_id", "notes", "checkpoint")


def upsert_step(teacher: dict, track_id: str, data: dict, step_id: Optional[str] = None) -> dict:
    own_track(teacher["id"], track_id)
    row = {k: data[k] for k in STEP_FIELDS if k in data}
    if step_id:
        row["updated_at"] = "now"
        return db().table("reg_track_steps").update(row).eq("id", step_id).eq("track_id", track_id).execute().data[0]
    row["track_id"] = track_id
    return db().table("reg_track_steps").insert(row).execute().data[0]


def delete_step(teacher: dict, track_id: str, step_id: str) -> None:
    own_track(teacher["id"], track_id)
    db().table("reg_track_steps").delete().eq("id", step_id).eq("track_id", track_id).execute()


def _step_of_track(track_id: str, step_id: str) -> dict:
    s = one(db().table("reg_track_steps").select("*").eq("id", step_id).eq("track_id", track_id))
    if not s:
        raise LookupError("Step not found")
    return s


def add_item(teacher: dict, track_id: str, step_id: str, data: dict,
             file_bytes: Optional[bytes] = None, filename: Optional[str] = None,
             content_type: Optional[str] = None) -> dict:
    own_track(teacher["id"], track_id)
    _step_of_track(track_id, step_id)
    row = {"step_id": step_id, "kind": data["kind"], "title": data.get("title"), "body": data.get("body"),
           "past_question_id": data.get("past_question_id"), "sort": data.get("sort", 0)}
    if file_bytes is not None:
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in (filename or "file"))
        path = f"{track_id}/{step_id}/{safe}"
        db().storage.from_(TRACK_BUCKET).upload(path, file_bytes, {"content-type": content_type or "application/octet-stream", "upsert": "true"})
        row["storage_path"] = path
    return db().table("reg_track_items").insert(row).execute().data[0]


def delete_item(teacher: dict, track_id: str, item_id: str) -> None:
    own_track(teacher["id"], track_id)
    item = one(db().table("reg_track_items").select("id,storage_path,reg_track_steps!inner(track_id)")
               .eq("id", item_id).eq("reg_track_steps.track_id", track_id))
    if not item:
        raise LookupError("Item not found")
    if item.get("storage_path"):
        try:
            db().storage.from_(TRACK_BUCKET).remove([item["storage_path"]])
        except Exception:  # noqa: BLE001
            pass
    db().table("reg_track_items").delete().eq("id", item_id).execute()


def clone_track(teacher: dict, track_id: str, exam_year: Optional[int] = None) -> dict:
    """One-click copy for the next exam year: dates shift by a year, content is
    copied (files are shared, not duplicated), the copy starts as a draft."""
    src = track_detail(own_track(teacher["id"], track_id)["id"])
    shift = (exam_year or src["exam_year"] + 1) - src["exam_year"]

    def move(d: str) -> str:
        x = date.fromisoformat(d)
        try:
            return x.replace(year=x.year + shift).isoformat()
        except ValueError:  # 29 Feb
            return (x.replace(day=28, year=x.year + shift)).isoformat()

    new = db().table("reg_tracks").insert({
        "teacher_id": teacher["id"], "subject_id": src["subject_id"], "exam_year": src["exam_year"] + shift,
        "title": src["title"], "description": src["description"], "variant": src["variant"],
        "price_fcfa": src["price_fcfa"], "start_date": move(src["start_date"]),
        "exam_date": move(src["exam_date"]), "status": "draft", "cloned_from": src["id"],
    }).execute().data[0]
    for s in src["steps"]:
        ns = db().table("reg_track_steps").insert({k: s.get(k) for k in STEP_FIELDS} | {"track_id": new["id"]}).execute().data[0]
        if s["items"]:
            db().table("reg_track_items").insert([
                {k: i.get(k) for k in ("kind", "title", "body", "storage_path", "past_question_id", "sort")}
                | {"step_id": ns["id"]} for i in s["items"]
            ]).execute()
    return new


# --------------------------------------------------------------------------
# Catalogue and enrolment (FR-C1, FR-C2, FR-P1)
# --------------------------------------------------------------------------
def catalogue(subject_id: Optional[str] = None) -> list:
    q = db().table("reg_tracks").select("*").eq("status", "published").gte("exam_date", today().isoformat())
    if subject_id:
        q = q.eq("subject_id", subject_id)
    tracks = q.order("start_date").execute().data or []
    if not tracks:
        return []
    teachers = {p["user_id"]: p for p in (db().table("reg_teacher_profiles").select("*")
                                          .in_("user_id", list({t["teacher_id"] for t in tracks})).execute().data or [])}
    counts: dict = {}
    for e in (db().table("reg_enrolments").select("track_id").in_("track_id", [t["id"] for t in tracks])
              .eq("status", "active").execute().data or []):
        counts[e["track_id"]] = counts.get(e["track_id"], 0) + 1
    out = []
    for t in tracks:
        p = teachers.get(t["teacher_id"])
        if not p or p["verification_status"] != "verified":
            continue
        out.append({**{k: t[k] for k in ("id", "title", "description", "subject_id", "exam_year", "variant",
                                         "price_fcfa", "start_date", "exam_date")},
                    "teacher": public_teacher(p), "enrolled": counts.get(t["id"], 0)})
    return out


def recommended(user_id: str) -> list:
    weak = set(profile.weak_subject_ids(user_id))
    return [t for t in catalogue() if t["subject_id"] in weak]


def start_enrolment(user: dict, track_id: str, parent_phone: str) -> dict:
    from app.edu import payments
    t = one(db().table("reg_tracks").select("*").eq("id", track_id).eq("status", "published"))
    if not t:
        raise LookupError("Track not found")
    existing = one(db().table("reg_enrolments").select("*").eq("track_id", track_id).eq("student_id", user["id"]))
    if existing and existing["status"] == "active":
        raise ValueError("Already enrolled")
    from app.edu.phone import normalize_phone
    parent = normalize_phone(parent_phone)
    if not parent:
        raise ValueError("A valid parent phone number is required")
    if existing:
        enrol = db().table("reg_enrolments").update({"status": "pending", "parent_phone": parent}) \
            .eq("id", existing["id"]).execute().data[0]
    else:
        enrol = db().table("reg_enrolments").insert({
            "track_id": track_id, "student_id": user["id"], "parent_phone": parent,
        }).execute().data[0]
    purchase = payments.buy_track(user, t, enrol["id"], parent)
    db().table("reg_enrolments").update({"payment_id": purchase["payment"]["id"]}).eq("id", enrol["id"]).execute()
    return {"enrolment_id": enrol["id"], **purchase}


def my_enrolment(user_id: str, track_id: str) -> Optional[dict]:
    return one(db().table("reg_enrolments").select("*").eq("track_id", track_id)
               .eq("student_id", user_id).eq("status", "active"))


def _progress(enrolment_id: str) -> set:
    return {r["step_id"] for r in (db().table("reg_step_progress").select("step_id")
                                   .eq("enrolment_id", enrolment_id).execute().data or [])}


def pace(track: dict, steps: list, done: set) -> dict:
    unlocked = sum(1 for s in steps if unlock_date(track, s["week_number"]) <= today())
    completed = sum(1 for s in steps if s["id"] in done)
    return {"unlocked_steps": unlocked, "completed_steps": completed, "total_steps": len(steps),
            "status": "behind" if completed < unlocked - 1 else "on_track"}


def student_track(user: dict, track_id: str) -> dict:
    enrol = my_enrolment(user["id"], track_id)
    if not enrol:
        raise PermissionError("Not enrolled in this track")
    t = track_detail(track_id)
    done = _progress(enrol["id"])
    unlocked_weeks = [s["week_number"] for s in t["steps"] if unlock_date(t, s["week_number"]) <= today()]
    can_open_through = (max(unlocked_weeks) if unlocked_weeks else 0) + RUN_AHEAD_STEPS
    steps = []
    for s in t["steps"]:
        open_ = s["week_number"] <= can_open_through
        steps.append({
            "id": s["id"], "week_number": s["week_number"], "title": s["title"], "topic": s["topic"],
            "unlock_date": s["unlock_date"], "open": open_, "completed": s["id"] in done,
        })
    teacher = teacher_profile(t["teacher_id"]) or {}
    return {"track": {k: t[k] for k in ("id", "title", "description", "subject_id", "exam_year", "start_date", "exam_date")},
            "teacher": public_teacher(teacher), "steps": steps, "pace": pace(t, t["steps"], done),
            "enrolment_id": enrol["id"]}


def student_step(user: dict, track_id: str, step_id: str) -> dict:
    view = student_track(user, track_id)
    meta = next((s for s in view["steps"] if s["id"] == step_id), None)
    if not meta:
        raise LookupError("Step not found")
    if not meta["open"]:
        raise PermissionError(f"This step opens on {meta['unlock_date']}")
    step = _step_of_track(track_id, step_id)
    items = (db().table("reg_track_items").select("id,kind,title,body,past_question_id,storage_path,sort")
             .eq("step_id", step_id).order("sort").execute().data) or []
    qids = [i["past_question_id"] for i in items if i.get("past_question_id")]
    questions = {q["id"]: q for q in (db().table("reg_past_questions").select("id,number,text,marks,marking_scheme")
                                      .in_("id", qids).execute().data if qids else [])}
    out_items = []
    for i in items:
        item = {k: i[k] for k in ("id", "kind", "title", "body", "sort")}
        q = questions.get(i.get("past_question_id"))
        if q:
            item["question"] = {"id": q["id"], "number": q["number"], "text": q["text"], "marks": q["marks"]}
            if i["kind"] == "worked_question":
                item["question"]["marking_scheme"] = q["marking_scheme"]
        item["has_file"] = bool(i.get("storage_path"))
        out_items.append(item)
    return {**{k: step[k] for k in ("id", "week_number", "title", "topic", "notes", "checkpoint")},
            "completed": meta["completed"], "items": out_items}


def complete_step(user: dict, track_id: str, step_id: str) -> dict:
    view = student_track(user, track_id)
    meta = next((s for s in view["steps"] if s["id"] == step_id), None)
    if not meta or not meta["open"]:
        raise PermissionError("This step isn't open yet")
    db().table("reg_step_progress").upsert({"enrolment_id": view["enrolment_id"], "step_id": step_id},
                                           on_conflict="enrolment_id,step_id", ignore_duplicates=True).execute()
    return student_track(user, track_id)["pace"]


def item_file(user: dict, track_id: str, item_id: str) -> tuple:
    """Stream an item's file in-app. PDFs get the student's phone number stamped
    on every page so a forwarded copy is traceable (FR-T2)."""
    item = one(db().table("reg_track_items").select("*, reg_track_steps!inner(id,track_id)")
               .eq("id", item_id).eq("reg_track_steps.track_id", track_id))
    if not item or not item.get("storage_path"):
        raise LookupError("File not found")
    student_step(user, track_id, item["reg_track_steps"]["id"])  # access + schedule check
    data = db().storage.from_(TRACK_BUCKET).download(item["storage_path"])
    name = item["storage_path"].rsplit("/", 1)[-1]
    if name.lower().endswith(".pdf"):
        data = watermark_pdf(data, f"Carati · {user['phone_number']} · personal copy")
        return data, "application/pdf"
    if name.lower().endswith((".mp3", ".m4a", ".ogg", ".aac", ".wav")):
        return data, "audio/mpeg" if name.lower().endswith(".mp3") else "audio/" + name.rsplit(".", 1)[-1]
    return data, "application/octet-stream"


def item_pages(user: dict, track_id: str, item_id: str, page: Optional[int] = None):
    """PDF items rendered page by page as watermarked images, for phones whose
    browser can't show a PDF inline. page=None -> {"pages": n}; else PNG bytes."""
    import fitz
    data, mime = item_file(user, track_id, item_id)
    if mime != "application/pdf":
        raise ValueError("This item is not a document")
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        if page is None:
            return {"pages": doc.page_count}
        if not 1 <= page <= doc.page_count:
            raise LookupError("No such page")
        pix = doc[page - 1].get_pixmap(matrix=fitz.Matrix(1.4, 1.4))
        return pix.tobytes("png")
    finally:
        doc.close()


def watermark_pdf(data: bytes, text: str) -> bytes:
    import fitz  # PyMuPDF
    doc = fitz.open(stream=data, filetype="pdf")
    for page in doc:
        r = page.rect
        for y in (0.25, 0.55, 0.85):
            page.insert_text((r.width * 0.12, r.height * y), text, fontsize=13, color=(0.6, 0.6, 0.6),
                             fill_opacity=0.35, stroke_opacity=0.35, morph=(fitz.Point(r.width * 0.12, r.height * y), fitz.Matrix(-25)))
        page.insert_text((24, r.height - 14), text, fontsize=8, color=(0.4, 0.4, 0.4))
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


# --------------------------------------------------------------------------
# Group Q&A (FR-T3): LLM answers first; teacher sees the digest and may post
# one clarification per week to everyone.
# --------------------------------------------------------------------------
async def ask_in_track(user: dict, track_id: str, question: str) -> list:
    from app.edu.chat_service import SYSTEM_PROMPT, _clean
    from app.edu.db import general_school_id, run
    from app.edu.retrieval import retrieve_context
    if not await run(my_enrolment, user["id"], track_id):
        raise PermissionError("Not enrolled in this track")
    ws = week_start().isoformat()
    q = await run(lambda: db().table("reg_track_posts").insert({
        "track_id": track_id, "author_id": user["id"], "kind": "question", "body": question.strip(), "week_start": ws,
    }).execute().data[0])
    context = await retrieve_context(general_school_id(), question)
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context:
        msgs.append({"role": "system", "content": "Relevant GCE material:\n" + context})
    msgs.append({"role": "user", "content": question})
    answer = _clean(await llm.complete(msgs, max_tokens=config.ANSWER_MAX_TOKENS))
    a = await run(lambda: db().table("reg_track_posts").insert({
        "track_id": track_id, "author_id": None, "kind": "answer", "body": answer, "reply_to": q["id"], "week_start": ws,
    }).execute().data[0])
    return [q, a]


def posts(track_id: str, week: Optional[str] = None) -> list:
    q = db().table("reg_track_posts").select("*").eq("track_id", track_id)
    if week:
        q = q.eq("week_start", week)
    return q.order("created_at").limit(500).execute().data or []


def post_clarification(teacher: dict, track_id: str, body: str, kind: str = "clarification") -> dict:
    own_track(teacher["id"], track_id)
    ws = week_start().isoformat()
    if kind == "clarification" and one(db().table("reg_track_posts").select("id").eq("track_id", track_id)
                                       .eq("kind", "clarification").eq("week_start", ws)):
        raise ValueError("You've already posted this week's clarification")
    return db().table("reg_track_posts").insert({
        "track_id": track_id, "author_id": teacher["id"], "kind": kind, "body": body.strip(), "week_start": ws,
    }).execute().data[0]


def weekly_digest(teacher: dict, track_id: str, week: Optional[str] = None) -> dict:
    t = own_track(teacher["id"], track_id)
    ws = week or week_start().isoformat()
    we = (date.fromisoformat(ws) + timedelta(days=7)).isoformat()
    track_qs = [p for p in posts(track_id, ws) if p["kind"] == "question"]
    students = [e["student_id"] for e in (db().table("reg_enrolments").select("student_id")
                                          .eq("track_id", track_id).eq("status", "active").execute().data or [])]
    topic_counts: dict = {}
    if students:
        rows = (db().table("reg_messages").select("topic_id,reg_syllabus_topics!reg_messages_topic_id_fkey(name)")
                .in_("user_id", students).eq("subject_id", t["subject_id"]).eq("role", "user")
                .gte("created_at", ws).lt("created_at", we).not_.is_("topic_id", "null").limit(5000).execute().data) or []
        for r in rows:
            name = (r.get("reg_syllabus_topics") or {}).get("name") or "Other"
            topic_counts[name] = topic_counts.get(name, 0) + 1
    clar = next((p for p in posts(track_id, ws) if p["kind"] == "clarification"), None)
    return {"week_start": ws, "track_questions": track_qs,
            "chat_topics": sorted(({"topic": k, "count": v} for k, v in topic_counts.items()), key=lambda x: -x["count"]),
            "clarification_posted": clar}


# --------------------------------------------------------------------------
# Teacher dashboard (FR-T4)
# --------------------------------------------------------------------------
def cohort(teacher: dict, track_id: str) -> dict:
    t = track_detail(own_track(teacher["id"], track_id)["id"])
    enrols = (db().table("reg_enrolments").select("id,student_id,start_date,reg_users(phone_number)")
              .eq("track_id", track_id).eq("status", "active").execute().data) or []
    since = (today() - timedelta(days=7)).isoformat()
    students = []
    for e in enrols:
        sid = e["student_id"]
        done = _progress(e["id"])
        heat = profile.subject_map(sid, t["subject_id"])
        q_week = (db().table("reg_messages").select("id", count="exact").eq("user_id", sid)
                  .eq("role", "user").gte("created_at", since).execute().count) or 0
        a_week = (db().table("reg_attempts").select("id", count="exact").eq("user_id", sid)
                  .gte("created_at", since).execute().count) or 0
        students.append({
            "student_id": sid, "phone": mask_phone((e.get("reg_users") or {}).get("phone_number")),
            "pace": pace(t, t["steps"], done),
            "heat": heat["counts"], "coverage": heat["coverage"],
            "weak_topics": [x["name"] for x in heat["topics"] if x["state"] == "weak"][:5],
            "last_week": {"questions": q_week, "attempts": a_week},
        })
    behind = [s for s in students if s["pace"]["status"] == "behind"]
    return {"track_id": track_id, "students": students, "falling_behind": behind}


def marking_queue(teacher: dict) -> list:
    tracks = [t["id"] for t in (db().table("reg_tracks").select("id").eq("teacher_id", teacher["id"]).execute().data or [])]
    if not tracks:
        return []
    return (db().table("reg_attempts").select("*, reg_past_questions(number,text,marks,marking_scheme)")
            .in_("track_id", tracks).eq("marked_by", "pending_teacher").order("created_at").execute().data) or []


def teacher_mark(teacher: dict, attempt_id: str, marks_awarded: float, feedback: str) -> dict:
    from app.edu.papers_service import _record_attempt_stats
    a = one(db().table("reg_attempts").select("*, reg_past_questions(id,topic_id,paper_id,marks,reg_past_papers(id,subject_id))")
            .eq("id", attempt_id))
    if not a or a["marked_by"] != "pending_teacher":
        raise LookupError("Attempt not found")
    own_track(teacher["id"], a["track_id"])
    q = a.get("reg_past_questions") or {}
    total = float(a.get("marks_total") or q.get("marks") or 0)
    awarded = max(0.0, min(float(marks_awarded), total or float(marks_awarded)))
    row = db().table("reg_attempts").update({
        "marks_awarded": awarded, "marks_total": total, "feedback": feedback, "marked_by": "teacher",
        "teacher_id": teacher["id"], "marked_at": "now",
    }).eq("id", attempt_id).execute().data[0]
    _record_attempt_stats(a["user_id"], q, q.get("reg_past_papers") or {}, awarded, total)
    return row


def earnings(teacher: dict) -> dict:
    p = teacher_profile(teacher["id"]) or {}
    share = float(p.get("revenue_share") or 0.7)
    tracks = db().table("reg_tracks").select("id,title,price_fcfa,exam_year,status").eq("teacher_id", teacher["id"]).execute().data or []
    ids = [t["id"] for t in tracks]
    paid = (db().table("reg_payments").select("track_id,amount_fcfa,status,paid_at")
            .in_("track_id", ids).eq("type", "track").in_("status", ["paid", "refunded"]).execute().data if ids else []) or []
    per = {t["id"]: {"track_id": t["id"], "title": t["title"], "enrolments": 0, "gross_fcfa": 0, "refunds": 0} for t in tracks}
    for pmt in paid:
        rec = per.get(pmt["track_id"])
        if not rec:
            continue
        if pmt["status"] == "paid":
            rec["enrolments"] += 1
            rec["gross_fcfa"] += pmt["amount_fcfa"]
        else:
            rec["refunds"] += 1
    for rec in per.values():
        rec["share_fcfa"] = int(rec["gross_fcfa"] * share)
    payouts = (db().table("reg_payouts").select("*").eq("teacher_id", teacher["id"])
               .order("period_end", desc=True).execute().data) or []
    earned = sum(r["share_fcfa"] for r in per.values())
    paid_out = sum(x["amount_fcfa"] for x in payouts if x["status"] == "paid")
    return {"revenue_share": share, "tracks": list(per.values()), "earned_fcfa": earned,
            "paid_out_fcfa": paid_out, "balance_fcfa": earned - paid_out, "payouts": payouts}


# --------------------------------------------------------------------------
# Refunds (FR-M3)
# --------------------------------------------------------------------------
def request_refund(user: dict, track_id: str) -> dict:
    enrol = my_enrolment(user["id"], track_id)
    if not enrol:
        raise LookupError("No active enrolment")
    activated = date.fromisoformat((enrol.get("activated_at") or enrol["created_at"])[:10])
    steps_done = len(_progress(enrol["id"]))
    if (today() - activated).days > config.REFUND_WINDOW_DAYS or steps_done > config.REFUND_MAX_STEPS_DONE:
        raise ValueError("Refunds are available within 7 days of purchase if fewer than two steps are completed")
    db().table("reg_enrolments").update({"status": "refunded"}).eq("id", enrol["id"]).execute()
    if enrol.get("payment_id"):
        db().table("reg_payments").update({"status": "refunded"}).eq("id", enrol["payment_id"]).execute()
    return {"status": "refunded", "note": "The refund is sent to the paying parent's wallet."}
