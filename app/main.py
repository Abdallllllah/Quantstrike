from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from typing import Optional, List, Any
import shutil
import os
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(
    title="Quant AI Tutor",
    description="Multi-Subject AI Tutor RAG System with WhatsApp Integration",
    version="2.0.0"
)

# ==========================
# ENABLE CORS FOR ALL ORIGINS
# ==========================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # allow all origins
    allow_credentials=False,  # must be False when using "*"
    allow_methods=["*"],
    allow_headers=["*"],
)
# ==========================================
# PYDANTIC MODELS FOR API DOCS
# ==========================================

class RAGQueryRequest(BaseModel):
    """Request body for RAG query endpoint."""
    user_id: str = Field(..., description="Unique identifier for the user", example="user123")
    message: str = Field(..., description="The question or message to ask", example="What is Newton's second law?")
    school_id: str = Field(..., description="School identifier (slug or UUID) for data isolation", example="greenwood-high")
    subject: str = Field(..., description="Subject name or slug", example="physics")
    class_level: str = Field(..., alias="class", description="Class/grade level", example="a-level")
    
    class Config:
        populate_by_name = True


class SchoolCreateRequest(BaseModel):
    """Request body for creating a school."""
    name: str = Field(..., description="School name", example="Greenwood High School")
    slug: Optional[str] = Field(
        None,
        description="URL-safe identifier for data isolation. Auto-generated from name if omitted.",
        example="greenwood-high",
    )


class ClassCreateRequest(BaseModel):
    """Request body for creating a class under a subject."""
    name: str = Field(..., description="Class/grade name", example="A-Level")
    subject: str = Field(..., description="Subject name or slug the class belongs to", example="physics")
    description: Optional[str] = Field(None, description="Optional description")


class RAGQueryResponse(BaseModel):
    """Response body for RAG query endpoint."""
    user_id: str = Field(..., description="The user's identifier")
    response: str = Field(..., description="The AI-generated response")
    school: Optional[str] = Field(None, description="School name")
    subject: Optional[str] = Field(None, description="Current subject name")
    class_level: Optional[str] = Field(None, alias="class", description="Current class name")
    sources: List[str] = Field(default_factory=list, description="List of source documents used")
    metadata: Optional[dict] = Field(None, description="Additional response metadata")
    error: Optional[str] = Field(None, description="Error message if request failed")
    
    class Config:
        populate_by_name = True

# NOTE: rag_original imports are done lazily in endpoints to reduce memory usage


# Import WhatsApp router
try:
    from app.whatsapp import whatsapp_router
    WHATSAPP_ENABLED = True
except ImportError:
    WHATSAPP_ENABLED = False

Path("uploads").mkdir(exist_ok=True)



# Include WhatsApp router if available
if WHATSAPP_ENABLED:
    app.include_router(whatsapp_router)

# ==========================================
# GATEWAY ROUTERS (merged from quantstrikeController)
# Master chat, auth, practice, past-paper retrieval, vision/audio extraction.
# Each router owns its own /api/* prefix. controller.py/extract call other
# endpoints in THIS app over localhost ($BASE_URL / $PORT), so everything runs
# as one Render service. The old /api/rag proxy was dropped — the worker's
# /api/rag above is canonical.
# ==========================================
from app.gateway.routes import (
    controller as gw_controller,
    authentication as gw_authentication,
    onboardMultipleUsers as gw_onboard,
    insertMessage as gw_messages,
    practice as gw_practice,
    extract_question_paper_data as gw_extract,
    search as gw_search,
    upload as gw_upload,
    triggerDownload as gw_download,
    getQuestionFromPicture as gw_vision_questions,
    speechToText as gw_audio,
    convertPDFToText as gw_pdf2word,
)

for _gw in (
    gw_controller, gw_authentication, gw_onboard, gw_messages, gw_practice,
    gw_extract, gw_search, gw_upload, gw_download,
    gw_vision_questions, gw_audio, gw_pdf2word,
):
    app.include_router(_gw.router)

# ==========================================
# SCHOOL APP (edu) — school/teacher/student accounts, roles, assignments.
# Builds on the reg_* multi-tenant tables; students' RAG answers stay scoped
# to their school via the existing /api/rag isolation.
# ==========================================
from app.edu.routes import router as edu_router
app.include_router(edu_router)

# Mount static files (CSS, JS, etc.)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def read_root(request: Request):
    if request.method == "HEAD":
        return HTMLResponse(content="", status_code=200)
    return templates.TemplateResponse(request, "index.html")


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    
    try:
        # Save to temporary local file
        temp_location = f"uploads/{file.filename}"
        with open(temp_location, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Backup to cloud storage (Supabase)
        cloud_url = None
        try:
            from app.db.storage import get_storage_client
            storage = get_storage_client()
            success, result = storage.upload_file(temp_location)
            if success:
                cloud_url = result
        except Exception as storage_error:
            print(f"Cloud storage backup failed (non-fatal): {storage_error}")
        
        # Note: Document ingestion for RAG is handled separately via /api/ingest
        # The upload endpoint just saves the file to cloud storage
        
        return JSONResponse({
            "filename": file.filename, 
            "status": "success",
            "message": "File uploaded to cloud storage. Use the Knowledge Base section with Subject/Class to index for RAG.",
            "cloud_backup": cloud_url is not None
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents")
async def list_documents():
    """List all uploaded PDF documents."""
    try:
        files = []
        if os.path.exists("uploads"):
            for filename in os.listdir("uploads"):
                if filename.endswith(".pdf"):
                    files.append(filename)
        return JSONResponse({"files": files})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/documents/{filename}")
async def delete_document(filename: str):
    """Delete a document."""
    try:
        file_path = os.path.join("uploads", filename)
        if os.path.exists(file_path):
            os.remove(file_path)
            
            # Note: Cloud storage deletion is handled separately
            
            return JSONResponse({"status": "success", "message": f"{filename} deleted"})
        else:
            raise HTTPException(status_code=404, detail="File not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/query")
async def query_documents(request: Request):
    """Query endpoint - redirects users to use the context-aware RAG."""
    try:
        data = await request.json()
        query = data.get("query")
        if not query:
            raise HTTPException(status_code=400, detail="Query is required")
        
        # Return a helpful message pointing to the proper context-aware flow
        return JSONResponse({
            "answer": "To search your documents, please use the Knowledge Base section to select a Subject and Class first, then upload your PDFs. After that, use the /api/rag endpoint with subject and class context for accurate answers.",
            "sources": [],
            "info": "This endpoint requires context. Use POST /api/rag with user_id, message, subject, and class fields."
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))






@app.post("/api/generate-questions")
async def generate_questions(request: Request):
    """Generate practice questions - lightweight version."""
    try:
        data = await request.json()
        topic = data.get("topic")
        if not topic:
            raise HTTPException(status_code=400, detail="Topic is required")
        
        difficulty = data.get("difficulty", "medium")
        count = data.get("count", 5)
        
        # Return a helpful message
        return JSONResponse({
            "topic": topic,
            "difficulty": difficulty,
            "count": count,
            "questions": "To generate practice questions, please first upload documents using the Knowledge Base section (select Subject and Class, then upload PDFs). Once your documents are indexed, practice question generation will work."
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/mark-answer")
async def mark_answer(request: Request):
    """Mark a student's answer - lightweight version."""
    try:
        data = await request.json()
        question = data.get("question")
        student_answer = data.get("student_answer")
        
        if not question:
            raise HTTPException(status_code=400, detail="Question is required")
        if not student_answer:
            raise HTTPException(status_code=400, detail="Student answer is required")
        
        max_marks = data.get("max_marks", 5)
        
        # Return a helpful message
        return JSONResponse({
            "question": question,
            "student_answer": student_answer,
            "max_marks": max_marks,
            "marking_result": "To mark answers, please first upload your course materials using the Knowledge Base section (select Subject and Class, then upload PDFs). Once your marking schemes are indexed, answer checking will work."
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# NEW ENDPOINTS FOR MULTI-SUBJECT TUTOR
# ==========================================

@app.get("/api/health")
async def health_check():
    """Health check endpoint for monitoring."""
    return JSONResponse({
        "status": "healthy",
        "version": "2.0.0",
        "whatsapp_enabled": WHATSAPP_ENABLED,
    })


@app.post("/api/rag", response_model=RAGQueryResponse)
async def rag_query(request_body: RAGQueryRequest):
    """
    Query the RAG pipeline for an AI-generated response.
    
    Send a message with context (subject/class) to get an AI-powered answer
    based on your uploaded documents.
    """
    try:
        from app.controller.orchestrator import get_orchestrator
        orchestrator = get_orchestrator()
        
        result = await orchestrator.process_message(
            user_id=request_body.user_id,
            message=request_body.message,
            school_id=request_body.school_id,
            subject=request_body.subject,
            class_level=request_body.class_level,
        )
        
        return JSONResponse(result)
    except ImportError as e:
        raise HTTPException(
            status_code=503, 
            detail=f"RAG service not available: {e}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/schools")
async def list_schools():
    """List all available schools."""
    try:
        from app.db.supabase import get_supabase_client
        client = get_supabase_client()
        schools = client.get_all_schools()
        
        return JSONResponse({
            "schools": [
                {
                    "id": str(s.id),
                    "name": s.name,
                    "slug": s.slug,
                }
                for s in schools
            ]
        })
    except ImportError:
        return JSONResponse({
            "schools": [],
            "error": "Supabase not configured"
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/schools", status_code=201)
async def create_school(school: SchoolCreateRequest):
    """
    Register a new school (tenant) for multi-tenant data isolation.

    Provide a `name` and optionally a `slug`. If the slug is omitted it is
    derived from the name (lowercased, non-alphanumeric runs collapsed to
    hyphens). Slugs must be unique.
    """
    import re

    try:
        from app.db.supabase import get_supabase_client
        from app.db.models import SchoolCreate
        client = get_supabase_client()

        name = school.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="School name is required")

        # Derive slug from name when not supplied.
        raw_slug = school.slug.strip() if school.slug else name
        slug = re.sub(r"[^a-z0-9]+", "-", raw_slug.lower()).strip("-")
        if not slug:
            raise HTTPException(status_code=400, detail="Could not derive a valid slug from the provided name/slug")

        # Reject duplicate slugs up front for a clean 409 instead of a DB error.
        if client.get_school_by_slug(slug):
            raise HTTPException(status_code=409, detail=f"School with slug '{slug}' already exists")

        created = client.create_school(SchoolCreate(name=name, slug=slug))

        return JSONResponse(
            status_code=201,
            content={
                "status": "success",
                "school": {
                    "id": str(created.id),
                    "name": created.name,
                    "slug": created.slug,
                    "is_active": created.is_active,
                },
            },
        )
    except HTTPException:
        raise
    except ImportError:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/subjects")
async def list_subjects():
    """List all available subjects."""
    try:
        from app.db.supabase import get_supabase_client
        client = get_supabase_client()
        subjects = client.get_all_subjects()
        
        return JSONResponse({
            "subjects": [
                {
                    "id": str(s.id),
                    "name": s.name,
                    "slug": s.slug,
                }
                for s in subjects
            ]
        })
    except ImportError:
        return JSONResponse({
            "subjects": [],
            "error": "Supabase not configured"
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/classes/{subject_id}")
async def list_classes(subject_id: str):
    """List all classes for a specific subject."""
    try:
        from app.db.supabase import get_supabase_client
        from uuid import UUID
        client = get_supabase_client()
        classes = client.get_classes_by_subject(UUID(subject_id))
        
        return JSONResponse({
            "classes": [
                {
                    "id": str(c.id),
                    "name": c.name,
                    "description": c.description,
                }
                for c in classes
            ]
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/classes", status_code=201)
async def create_class(payload: ClassCreateRequest):
    """
    Create a class under a subject (writes to reg_classes).

    Accepts the subject by name or slug — no UUID needed. This is the
    canonical class-creation endpoint; the old gateway `/api/classes` route
    (which wrote to a separate Supabase project) was removed in the merge.
    """
    try:
        from app.db.supabase import get_supabase_client
        client = get_supabase_client()

        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Class name is required")

        subject_obj = client.get_subject_by_slug(payload.subject)
        if not subject_obj:
            raise HTTPException(status_code=404, detail=f"Subject '{payload.subject}' not found")

        created = client.create_class(
            name=name,
            subject_id=subject_obj.id,
            description=payload.description,
        )

        return JSONResponse(
            status_code=201,
            content={
                "status": "success",
                "class": {
                    "id": str(created.id),
                    "name": created.name,
                    "subject": subject_obj.name,
                    "description": created.description,
                },
            },
        )
    except HTTPException:
        raise
    except ImportError:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# SCHOOL DOCUMENT MANAGEMENT API
# ==========================================

@app.post("/api/schools/{school_slug}/documents")
async def upload_school_document(
    school_slug: str,
    file: UploadFile = File(..., description="PDF file to upload"),
    subject: str = Form(..., description="Subject slug (e.g. 'physics')"),
    class_level: str = Form(..., alias="class", description="Class name (e.g. 'Grade 10')"),
    doc_type: str = Form("notes", description="Document type: notes, examples, past_paper, marking_scheme"),
):
    """
    Upload a PDF document for a school's knowledge base.
    
    The document will be chunked, embedded, and stored in the school's
    isolated vector store. Only students from this school can retrieve it.
    
    **Accepts subject and class by name/slug** — no UUIDs needed.
    """
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    
    try:
        from app.db.supabase import get_supabase_client
        from uuid import UUID
        client = get_supabase_client()
        
        # Resolve school
        school = client.get_school_by_slug(school_slug)
        if not school:
            raise HTTPException(status_code=404, detail=f"School '{school_slug}' not found")
        
        # Resolve subject
        subject_obj = client.get_subject_by_slug(subject)
        if not subject_obj:
            raise HTTPException(status_code=404, detail=f"Subject '{subject}' not found")
        
        # Resolve class
        classes = client.get_classes_by_subject(subject_obj.id)
        class_obj = None
        class_lower = class_level.lower()
        for cls in classes:
            if cls.name.lower() == class_lower or class_lower in cls.name.lower():
                class_obj = cls
                break
        
        if not class_obj:
            available = [c.name for c in classes]
            raise HTTPException(
                status_code=404,
                detail=f"Class '{class_level}' not found for subject '{subject}'. Available: {available}"
            )
        
        # Save file temporarily
        temp_dir = Path("uploads")
        temp_dir.mkdir(exist_ok=True)
        file_location = temp_dir / file.filename
        
        with open(file_location, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Ingest into school-scoped vector store
        from app.rag.ingestion import get_ingestion_service
        ingestion_service = get_ingestion_service()
        
        success, msg = await ingestion_service.ingest_document(
            file_path=str(file_location),
            school_id=str(school.id),
            subject_id=str(subject_obj.id),
            class_id=str(class_obj.id),
            doc_type=doc_type
        )
        
        if not success:
            raise HTTPException(status_code=500, detail=msg)
        
        return JSONResponse({
            "status": "success",
            "filename": file.filename,
            "school": school.name,
            "subject": subject_obj.name,
            "class": class_obj.name,
            "doc_type": doc_type,
            "message": msg
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/schools/{school_slug}/documents")
async def list_school_documents(
    school_slug: str,
    subject: Optional[str] = None,
    class_level: Optional[str] = None,
):
    """
    List all documents uploaded by a school.
    
    Optionally filter by subject slug and/or class name.
    """
    try:
        from app.db.supabase import get_supabase_client
        client = get_supabase_client()
        
        # Resolve school
        school = client.get_school_by_slug(school_slug)
        if not school:
            raise HTTPException(status_code=404, detail=f"School '{school_slug}' not found")
        
        # Optional subject/class filters
        subject_id = None
        class_id = None
        
        if subject:
            subject_obj = client.get_subject_by_slug(subject)
            if subject_obj:
                subject_id = subject_obj.id
                
                if class_level:
                    classes = client.get_classes_by_subject(subject_obj.id)
                    class_lower = class_level.lower()
                    for cls in classes:
                        if cls.name.lower() == class_lower or class_lower in cls.name.lower():
                            class_id = cls.id
                            break
        
        documents = client.get_documents_by_school(
            school_id=school.id,
            subject_id=subject_id,
            class_id=class_id,
        )
        
        return JSONResponse({
            "school": school.name,
            "count": len(documents),
            "documents": [
                {
                    "id": str(doc.id),
                    "filename": doc.filename,
                    "doc_type": doc.doc_type,
                    "chunk_count": doc.chunk_count,
                    "is_indexed": doc.is_indexed,
                    "created_at": doc.created_at.isoformat(),
                }
                for doc in documents
            ]
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/schools/{school_slug}/documents/{document_id}")
async def delete_school_document(school_slug: str, document_id: str):
    """
    Delete a document and all its embeddings from a school's knowledge base.
    
    This permanently removes the document and its vector embeddings.
    """
    try:
        from app.db.supabase import get_supabase_client
        from uuid import UUID
        client = get_supabase_client()
        
        # Resolve school
        school = client.get_school_by_slug(school_slug)
        if not school:
            raise HTTPException(status_code=404, detail=f"School '{school_slug}' not found")
        
        # Verify document belongs to this school
        doc_uuid = UUID(document_id)
        docs = client.get_documents_by_school(school_id=school.id)
        doc_match = next((d for d in docs if d.id == doc_uuid), None)
        
        if not doc_match:
            raise HTTPException(
                status_code=404,
                detail=f"Document '{document_id}' not found for school '{school_slug}'"
            )
        
        # Delete document and embeddings
        client.delete_document(doc_uuid)
        
        return JSONResponse({
            "status": "success",
            "message": f"Deleted '{doc_match.filename}' and all its embeddings",
            "document_id": document_id,
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Keep legacy /api/ingest for backward compatibility
@app.post("/api/ingest", include_in_schema=False)
async def ingest_document_legacy(
    file: UploadFile = File(...),
    school_id: str = Form(...),
    subject_id: str = Form(...),
    class_id: str = Form(...),
    doc_type: str = Form("notes")
):
    """Legacy ingest endpoint (accepts UUIDs). Use /api/schools/{slug}/documents instead."""
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    
    try:
        temp_dir = Path("uploads")
        temp_dir.mkdir(exist_ok=True)
        file_location = temp_dir / file.filename
        
        with open(file_location, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        from app.rag.ingestion import get_ingestion_service
        ingestion_service = get_ingestion_service()
        
        success, msg = await ingestion_service.ingest_document(
            file_path=str(file_location),
            school_id=school_id,
            subject_id=subject_id,
            class_id=class_id,
            doc_type=doc_type
        )
        
        if not success:
            raise HTTPException(status_code=500, detail=msg)
        
        return JSONResponse({
            "filename": file.filename,
            "status": "success",
            "message": msg
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

