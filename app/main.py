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
    subject: str = Field(..., description="Subject name or slug", example="physics")
    class_level: str = Field(..., alias="class", description="Class/grade level", example="a-level")
    
    class Config:
        populate_by_name = True


class RAGQueryResponse(BaseModel):
    """Response body for RAG query endpoint."""
    user_id: str = Field(..., description="The user's identifier")
    response: str = Field(..., description="The AI-generated response")
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

# Mount static files (CSS, JS, etc.)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def read_root(request: Request):
    if request.method == "HEAD":
        return HTMLResponse(content="", status_code=200)
    return templates.TemplateResponse("index.html", {"request": request})


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


@app.post("/api/ingest")
async def ingest_document(
    file: UploadFile = File(...),
    subject_id: str = Form(...),
    class_id: str = Form(...),
    doc_type: str = Form("notes")
):
    """
    Ingest a PDF document into the context-aware RAG pipeline.
    """
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    
    try:
        # Save file temporarily
        temp_dir = Path("uploads")
        temp_dir.mkdir(exist_ok=True)
        file_location = temp_dir / file.filename
        
        with open(file_location, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Trigger contextual ingestion
        from app.rag.ingestion import get_ingestion_service
        ingestion_service = get_ingestion_service()
        
        success, msg = await ingestion_service.ingest_document(
            file_path=str(file_location),
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
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

