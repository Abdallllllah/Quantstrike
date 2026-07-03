from fastapi import APIRouter, HTTPException, Query, Response
import httpx
import logging
from urllib.parse import quote

router = APIRouter(prefix="/api/documents", tags=["Retrieval"])
logger = logging.getLogger("api_logger")

SUPABASE_BASE = "https://bdnvaahnvqrkqredddsv.supabase.co/storage/v1/object/public/GCEResources/"

@router.get("/download")
async def download_document(file_path: str = Query(...)):
    try:
        # If frontend accidentally sends full URL, strip base
        if file_path.startswith("http"):
            file_path = file_path.split("/public/")[1]

        encoded_path = "/".join(
            quote(segment) for segment in file_path.split("/")
        )

        file_url = SUPABASE_BASE + encoded_path

        async with httpx.AsyncClient() as client:
            resp = await client.get(file_url)

        if resp.status_code != 200:
            raise HTTPException(status_code=404, detail="File not found")

        file_name = file_path.split("/")[-1]

        return Response(
            content=resp.content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{file_name}"'
            },
        )

    except Exception as e:
        logger.error(f"Download failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")