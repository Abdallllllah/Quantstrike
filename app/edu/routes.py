"""
School / teacher / student app API (prefix /api/edu).

Auth is name + phone (no passwords). Student RAG isolation is handled by the
existing worker endpoints:
  - teacher note upload  -> POST /api/schools/{slug}/documents
  - student Q&A          -> POST /api/rag   (scoped by the student's school_id)
This module adds accounts/roles/enrollment and the assignments feature.
"""
import re
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from app.db.supabase import get_supabase_client
from app.edu.auth import make_token, get_current_user, require_role

router = APIRouter(prefix="/api/edu", tags=["School App"])


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
    }


def _get_user_by_phone(phone: str) -> Optional[dict]:
    res = _db().table("reg_users").select("*").eq("phone_number", phone).limit(1).execute()
    return res.data[0] if res.data else None


def _create_user(name: str, phone: str, role: str, school_id: Optional[str],
                 enrolled_by: Optional[str] = None) -> dict:
    payload = {
        "display_name": name,
        "phone_number": phone,
        "role": role,
        "school_id": school_id,
        "enrolled_by": enrolled_by,
    }
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


# ==========================================================================
# Request models
# ==========================================================================
class SchoolSignup(BaseModel):
    school_name: str = Field(..., example="Greenwood High")
    admin_name: str = Field(..., example="Jane Doe")
    phone: str = Field(..., example="+237600000000")


class TeacherSignup(BaseModel):
    name: str
    phone: str
    school: str = Field(..., description="School slug or name")


class StudentSignup(BaseModel):
    name: str
    phone: str
    school: str = Field(..., description="School slug or name")
    teacher_phone: Optional[str] = Field(None, description="Phone of the enrolling teacher")


class LoginRequest(BaseModel):
    phone: str
    name: Optional[str] = Field(None, description="Optional; checked against the account name if given")


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


# ==========================================================================
# AUTH
# ==========================================================================
@router.post("/schools/signup", status_code=201)
async def school_signup(payload: SchoolSignup):
    """Register a school and its first admin (name + phone)."""
    name = payload.admin_name.strip()
    phone = payload.phone.strip()
    if not name or not phone:
        raise HTTPException(status_code=400, detail="admin_name and phone are required")
    if _get_user_by_phone(phone):
        raise HTTPException(status_code=409, detail="An account with this phone already exists")

    client = get_supabase_client()
    school_name = payload.school_name.strip()
    slug = _slugify(school_name)
    if client.get_school_by_slug(slug):
        raise HTTPException(status_code=409, detail=f"School '{slug}' already exists")

    from app.db.models import SchoolCreate
    school = client.create_school(SchoolCreate(name=school_name, slug=slug))
    admin = _create_user(name, phone, "school_admin", str(school.id))
    return _auth_response(admin, school={"id": str(school.id), "name": school.name, "slug": school.slug})


@router.post("/teachers/signup", status_code=201)
async def teacher_signup(payload: TeacherSignup):
    """Register a teacher under an existing school."""
    phone = payload.phone.strip()
    if _get_user_by_phone(phone):
        raise HTTPException(status_code=409, detail="An account with this phone already exists")
    school = _resolve_school(payload.school)
    teacher = _create_user(payload.name.strip(), phone, "teacher", str(school.id))
    return _auth_response(teacher, school={"id": str(school.id), "name": school.name, "slug": school.slug})


@router.post("/students/signup", status_code=201)
async def student_signup(payload: StudentSignup):
    """Self-signup for a student, optionally naming the enrolling teacher."""
    phone = payload.phone.strip()
    if _get_user_by_phone(phone):
        raise HTTPException(status_code=409, detail="An account with this phone already exists")
    school = _resolve_school(payload.school)

    enrolled_by = None
    if payload.teacher_phone:
        teacher = _get_user_by_phone(payload.teacher_phone.strip())
        if not teacher or teacher.get("role") != "teacher" or str(teacher.get("school_id")) != str(school.id):
            raise HTTPException(status_code=404, detail="Enrolling teacher not found in this school")
        enrolled_by = teacher["id"]

    student = _create_user(payload.name.strip(), phone, "student", str(school.id), enrolled_by=enrolled_by)
    return _auth_response(student, school={"id": str(school.id), "name": school.name, "slug": school.slug})


@router.post("/login")
async def login(payload: LoginRequest):
    """Log in with phone (name optional, verified if provided)."""
    user = _get_user_by_phone(payload.phone.strip())
    if not user:
        raise HTTPException(status_code=404, detail="No account found for this phone")
    if payload.name and (user.get("display_name") or "").strip().lower() != payload.name.strip().lower():
        raise HTTPException(status_code=401, detail="Name does not match this phone")
    return _auth_response(user)


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return {"user": _public_user(user), "school": _school_dict(user.get("school_id"))}


# ==========================================================================
# ROSTER / ENROLLMENT
# ==========================================================================
@router.post("/students", status_code=201)
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
async def list_students(mine: bool = False, user: dict = Depends(require_role("teacher", "school_admin"))):
    """List students in the caller's school (mine=true → only those I enrolled)."""
    q = _db().table("reg_users").select("*").eq("role", "student").eq("school_id", user.get("school_id"))
    if mine:
        q = q.eq("enrolled_by", user["id"])
    res = q.order("created_at", desc=True).execute()
    return {"students": [_public_user(r) for r in res.data]}


@router.get("/teachers")
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
async def list_submissions(assignment_id: str, teacher: dict = Depends(require_role("teacher", "school_admin"))):
    db = _db()
    res = db.table("reg_assignments").select("teacher_id, school_id").eq("id", assignment_id).limit(1).execute()
    if not res.data or str(res.data[0]["school_id"]) != str(teacher.get("school_id")):
        raise HTTPException(status_code=404, detail="Assignment not found")
    subs = db.table("reg_assignment_submissions").select("*").eq("assignment_id", assignment_id).execute()
    return {"submissions": subs.data}


@router.post("/submissions/{submission_id}/grade")
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
