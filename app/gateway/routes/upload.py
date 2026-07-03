import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from app.gateway.database import supabase, STORAGE_BUCKET

router = APIRouter()
logger = logging.getLogger("api_logger")

@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    subject: str = Form(...),
    year: int = Form(...),
    paper_number: str = Form(...),
    curriculum: str = Form(...),
    level: str = Form(...)
):
    try:
        # 1. Normalize all inputs to UPPERCASE
        subject = subject.upper()
        paper_number = paper_number.upper()
        curriculum = curriculum.upper()
        level = level.upper()

        # ------------------------------------------------------------
        # 2. DUPLICATE CHECK LOGIC
        # ------------------------------------------------------------
        existing_doc = supabase.table("documents").select("*") \
            .eq("curriculum", curriculum) \
            .eq("level", level) \
            .eq("subject", subject) \
            .eq("year", year) \
            .eq("paper_number", paper_number) \
            .execute()

        if existing_doc.data:
            logger.warning(f"⚠️ Duplicate attempt: {curriculum} {subject} {year} {paper_number}")
            raise HTTPException(
                status_code=400, 
                detail=f"This paper ({curriculum} {subject} {paper_number} {year}) already exists in the system."
            )

        # ------------------------------------------------------------
        # 3. PROCEED WITH UPLOAD IF NO DUPLICATE FOUND
        # ------------------------------------------------------------
        file_content = await file.read()
        file_path = f"{curriculum}/{level}/{subject}/{year}/{paper_number}/{file.filename}"

        # Upload to Supabase Storage
        supabase.storage.from_(STORAGE_BUCKET).upload(
            path=file_path,
            file=file_content,
            file_options={
                "content-type": file.content_type,
                "upsert": "true"
            }
        )

        # Get Public URL
        file_url = supabase.storage.from_(STORAGE_BUCKET).get_public_url(file_path)

        # Insert Metadata into Table
        doc_data = {
            "subject": subject,
            "year": year,
            "paper_number": paper_number,
            "curriculum": curriculum,
            "level": level,
            "url": file_url,
            "timestamp": datetime.utcnow().isoformat()
        }

        result = supabase.table("documents").insert(doc_data).execute()

        logger.info(f"✅ Document successfully indexed: {file_path}")

        return {
            "message": "Upload successful",
            "storage_path": file_path,
            "data": result.data
        }

    except HTTPException as he:
        # Re-raise the 400 error specifically
        raise he
    except Exception as e:
        logger.error(f"❌ Upload Error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")