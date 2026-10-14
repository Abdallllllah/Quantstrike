"""Shared pieces for the Carati API routers: error mapping and request models."""
import functools

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.edu import allowance
from app.edu.chat_service import CapReached
from app.edu.papers_service import PaperCapReached


def error(status: int, code: str, message: str, details=None) -> JSONResponse:
    body = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(status_code=status, content=body)


def api(fn):
    """Map service exceptions to one JSON error shape:
    {"error": {"code", "message", "details"?}}. The daily cap is HTTP 429 with
    the reset time and the top-up offers in `details`."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except HTTPException:
            raise
        except CapReached as e:
            p = allowance.cap_payload(e.kind)
            return error(429, p["code"], p["message"], p)
        except PaperCapReached:
            p = allowance.cap_payload("paper")
            return error(429, p["code"], p["message"], p)
        except PermissionError as e:
            return error(403, "forbidden", str(e) or "Not allowed")
        except LookupError as e:
            return error(404, "not_found", str(e) or "Not found")
        except ValueError as e:
            return error(400, "bad_request", str(e))
        except Exception as e:  # noqa: BLE001 — surface the real cause
            print(f"[api] {fn.__name__}: {type(e).__name__}: {e}")
            return error(500, "server_error", f"{type(e).__name__}: {e}")
    return wrapper


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic")


async def read_attachment(file):
    """Turn an upload into (image_data_url, extracted_text, note)."""
    if file is None or not file.filename:
        return None, "", ""
    data = await file.read()
    mime = (file.content_type or "").lower()
    low = file.filename.lower()
    if mime.startswith("image") or low.endswith(IMAGE_EXTS):
        from app.gateway.routes.llm_clients import image_data_url
        return image_data_url(data, mime or "image/jpeg"), "", f"📷 {file.filename}"
    if mime == "application/pdf" or low.endswith(".pdf"):
        try:
            import fitz
            doc = fitz.open(stream=data, filetype="pdf")
            text = "\n".join(p.get_text() for p in doc).strip()
        except Exception:  # noqa: BLE001
            text = ""
        return None, (f"[Attached document '{file.filename}']\n{text[:8000]}" if text else ""), f"📄 {file.filename}"
    text = data.decode("utf-8", errors="ignore").strip()
    return None, (f"[Attached file '{file.filename}']\n{text[:8000]}" if text else ""), f"📎 {file.filename}"
