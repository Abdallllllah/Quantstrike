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
    title="Carati API",
    description="Carati — GCE A-Level study platform (students, teacher tracks, admin) plus the Tara business assistant",
    version="2.0.0",
    # Serve Swagger/ReDoc assets from cdnjs instead of the default jsdelivr CDN,
    # which is blocked (403 / ERR_BLOCKED_BY_RESPONSE) on some networks.
    docs_url=None,
    redoc_url=None,
)

from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html

_SWAGGER_JS = "https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/5.17.14/swagger-ui-bundle.min.js"
_SWAGGER_CSS = "https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/5.17.14/swagger-ui.min.css"
_REDOC_JS = "https://cdnjs.cloudflare.com/ajax/libs/redoc/2.1.5/redoc.standalone.min.js"


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} — Swagger UI",
        swagger_js_url=_SWAGGER_JS,
        swagger_css_url=_SWAGGER_CSS,
    )


@app.get("/redoc", include_in_schema=False)
async def custom_redoc():
    return get_redoc_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} — ReDoc",
        redoc_js_url=_REDOC_JS,
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
    insertMessage as gw_messages,
    practice as gw_practice,
    extract_question_paper_data as gw_extract,
    search as gw_search,
    triggerDownload as gw_download,
    getQuestionFromPicture as gw_vision_questions,
    speechToText as gw_audio,
    convertPDFToText as gw_pdf2word,
)

for _gw in (
    gw_controller, gw_messages, gw_practice,
    gw_extract, gw_search, gw_download,
    gw_vision_questions, gw_audio, gw_pdf2word,
):
    app.include_router(_gw.router)

# ==========================================
# CARATI (/api/edu) — the student app, teacher tracks and admin. See
# app/edu/api/*.py and caratidocs/DATABASE.md. One student identity
# (reg_users, phone-keyed) shared with WhatsApp.
# ==========================================
from app.edu.api import routers as carati_routers
for _r in carati_routers:
    app.include_router(_r)

# ==========================================
# BUSINESS (Tara) — voice-first shop assistant. Fully isolated (biz_* tables);
# does not touch the school/reg_ data. Channel-agnostic: web now, WhatsApp later.
# ==========================================
from app.business.routes import router as business_router
app.include_router(business_router)

# WhatsApp Cloud API channel for the business section (webhook + replies).
from app.business.whatsapp import router as business_whatsapp_router
app.include_router(business_whatsapp_router)

# Mount static files (CSS, JS, etc.)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def read_root(request: Request):
    if request.method == "HEAD":
        return HTMLResponse(content="", status_code=200)
    return templates.TemplateResponse(request, "index.html")


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


@app.post("/api/rag", response_model=RAGQueryResponse, include_in_schema=False)
async def rag_query(request_body: RAGQueryRequest, request: Request):
    """Internal: used by the WhatsApp gateway controller (/api/chat) over
    localhost. It bypasses the student allowance, so it is not public: callers
    must come from this server or send X-Internal-Token = INTERNAL_API_TOKEN."""
    token = os.getenv("INTERNAL_API_TOKEN")
    client_host = request.client.host if request.client else ""
    if not (client_host in ("127.0.0.1", "::1", "localhost")
            or (token and request.headers.get("x-internal-token") == token)):
        raise HTTPException(status_code=403, detail="Internal endpoint")
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

