from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import shutil
import os
from pathlib import Path

from app.rag_original import add_document as rag_add, delete_document as rag_delete

# Import WhatsApp router
try:
    from app.whatsapp import whatsapp_router
    WHATSAPP_ENABLED = True
except ImportError:
    WHATSAPP_ENABLED = False

Path("uploads").mkdir(exist_ok=True)

app = FastAPI(
    title="Quant AI Tutor",
    description="Multi-Subject AI Tutor RAG System with WhatsApp Integration",
    version="2.0.0"
)

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
        
        # Trigger RAG Ingestion using local temp file
        success, msg = await rag_add(temp_location)
        if not success:
            print(f"RAG Ingestion failed: {msg}")
        
        # Clean up local temp file after ingestion (optional in production)
        # os.remove(temp_location)
        
        return JSONResponse({
            "filename": file.filename, 
            "status": "success",
            "message": "File uploaded and processed successfully",
            "rag_status": msg,
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
            
            # Update RAG Index
            await rag_delete(filename)
            
            return JSONResponse({"status": "success", "message": f"{filename} deleted"})
        else:
            raise HTTPException(status_code=404, detail="File not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/query")
async def query_documents(request: Request):
    """Query the vector store and return top K chunks."""
    try:
        data = await request.json()
        query = data.get("query")
        if not query:
             raise HTTPException(status_code=400, detail="Query is required")
        
       
        from app.rag_simple import ask_question
        
        # Use full RAG
        result = ask_question(query)
        
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/exam-question")
async def get_exam_question(request: Request):
    """Look up a specific exam question by reference."""
    try:
        data = await request.json()
        reference = data.get("reference")
        if not reference:
            raise HTTPException(status_code=400, detail="Reference is required")
        
        from app.rag import lookup_exam_question
        result = lookup_exam_question(reference)
        
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/generate-questions")
async def generate_questions(request: Request):
    """Generate practice questions on a topic."""
    try:
        data = await request.json()
        topic = data.get("topic")
        if not topic:
            raise HTTPException(status_code=400, detail="Topic is required")
        
        difficulty = data.get("difficulty", "medium")
        count = data.get("count", 5)
        
        # Validate inputs
        if difficulty not in ["easy", "medium", "hard"]:
            difficulty = "medium"
        if not isinstance(count, int) or count < 1 or count > 10:
            count = 5
        
        from app.rag_simple import generate_practice_questions
        result = generate_practice_questions(topic, difficulty, count)
        
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/mark-answer")
async def mark_answer(request: Request):
    """Mark a student's answer and provide feedback."""
    try:
        data = await request.json()
        question = data.get("question")
        student_answer = data.get("student_answer")
        
        if not question:
            raise HTTPException(status_code=400, detail="Question is required")
        if not student_answer:
            raise HTTPException(status_code=400, detail="Student answer is required")
        
        max_marks = data.get("max_marks", 5)
        if not isinstance(max_marks, int) or max_marks < 1 or max_marks > 20:
            max_marks = 5
        
        from app.rag_original import mark_student_answer
        result = mark_student_answer(question, student_answer, max_marks)
        
        return JSONResponse(result)
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


@app.post("/api/rag")
async def rag_query(request: Request):
    
    try:
        data = await request.json()
        user_id = data.get("user_id")
        message = data.get("message")
        subject = data.get("subject")
        class_level = data.get("class")
        
        # Validate required parameters
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id is required")
        if not message:
            raise HTTPException(status_code=400, detail="message is required")
        if not subject:
            raise HTTPException(status_code=400, detail="subject is required")
        if not class_level:
            raise HTTPException(status_code=400, detail="class is required")
        
        from app.controller.orchestrator import get_orchestrator
        orchestrator = get_orchestrator()
        
        result = await orchestrator.process_message(
            user_id=user_id,
            message=message,
            subject=subject,
            class_level=class_level,
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

