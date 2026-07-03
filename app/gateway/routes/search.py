from fastapi import APIRouter, HTTPException, Query
from app.gateway.database import supabase
import logging

router = APIRouter(prefix="/api/documents", tags=["Retrieval"])
logger = logging.getLogger("api_logger")

@router.get("/retrieve")
async def retrieve_documents(
    subject: str = Query(...),
    curriculum: str = Query(...),
    paper_number: str = Query(...),
    year: int = Query(...),
    level: str = Query(...)
):
    try:
        # Match your DB exactly: 'MATHEMATICS', 'A-LEVEL', 'ONE'
        subject_upper = subject.strip().upper()
        curriculum_upper = curriculum.strip().upper()
        paper_upper = paper_number.strip().upper()
        level_upper = level.strip().upper()

        result = (
            supabase.table("documents")
            .select("url")
            .eq("subject", subject_upper)
            .eq("curriculum", curriculum_upper)
            .eq("paper_number", paper_upper)
            .eq("year", year)
            .eq("level", level_upper)
            .execute()
        )

        if not result.data:
            # If no match, try a looser check just in case of hidden spaces
            raise HTTPException(
                status_code=404, 
                detail=f"No document found for {subject_upper} {level_upper} {year} Paper {paper_upper}"
            )

        return {
            "status": "success",
            "file_url": result.data[0]["url"]
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Search failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")