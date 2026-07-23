"""
WhatsApp Cloud API channel for the business (Tara) section.

  GET  /api/business/whatsapp/webhook   Meta verification handshake
  POST /api/business/whatsapp/webhook   incoming messages

The shopkeeper is identified by their WhatsApp number (the tenant key), and the
account is created on first contact — no signup needed. Text, voice notes and
photos all flow through the same pipeline the web app uses (_process_message).

Reliability notes:
  * We answer 200 immediately and process in the background — Meta retries any
    webhook that isn't acknowledged quickly, which would otherwise double-record.
  * Every message id (wamid) is claimed once in biz_wa_events, so retries and
    duplicate deliveries can never record a sale twice.

Env (falls back to the generic WHATSAPP_* names used elsewhere):
  BUSINESS_WHATSAPP_TOKEN | WHATSAPP_TOKEN
  BUSINESS_WHATSAPP_PHONE_NUMBER_ID | WHATSAPP_PHONE_NUMBER_ID
  BUSINESS_WHATSAPP_VERIFY_TOKEN | WHATSAPP_VERIFY_TOKEN
  WHATSAPP_API_VERSION (default v21.0)
"""
import os
import httpx
from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
from fastapi.responses import PlainTextResponse

router = APIRouter(prefix="/api/business/whatsapp", tags=["Business (Tara) — WhatsApp"])

API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v21.0")
GRAPH = f"https://graph.facebook.com/{API_VERSION}"

_EXT_BY_MIME = {
    "audio/ogg": "ogg", "audio/opus": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a",
    "audio/aac": "aac", "audio/amr": "amr", "audio/wav": "wav", "audio/webm": "webm",
    "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
}


def _token() -> str:
    return os.getenv("BUSINESS_WHATSAPP_TOKEN") or os.getenv("WHATSAPP_TOKEN") or ""


def _verify_token() -> str:
    return os.getenv("BUSINESS_WHATSAPP_VERIFY_TOKEN") or os.getenv("WHATSAPP_VERIFY_TOKEN") or ""


def _default_phone_id() -> str:
    return os.getenv("BUSINESS_WHATSAPP_PHONE_NUMBER_ID") or os.getenv("WHATSAPP_PHONE_NUMBER_ID") or ""


# ==========================================================================
# webhook payload -> normalised messages
# ==========================================================================
def parse_webhook(payload: dict) -> list:
    """Flatten a WhatsApp webhook into a list of simple message dicts."""
    out = []
    for entry in (payload.get("entry") or []):
        for change in (entry.get("changes") or []):
            if change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
            names = {}
            for c in (value.get("contacts") or []):
                names[c.get("wa_id")] = ((c.get("profile") or {}).get("name") or "").strip()

            for m in (value.get("messages") or []):
                mtype = m.get("type")
                item = {
                    "wamid": m.get("id"),
                    "from": m.get("from"),
                    "name": names.get(m.get("from"), ""),
                    "type": mtype,
                    "phone_number_id": phone_number_id,
                    "text": "",
                    "media_id": None,
                    "mime": "",
                }
                if mtype == "text":
                    item["text"] = ((m.get("text") or {}).get("body") or "").strip()
                elif mtype in ("audio", "voice"):
                    a = m.get(mtype) or {}
                    item["media_id"] = a.get("id")
                    item["mime"] = a.get("mime_type") or "audio/ogg"
                elif mtype == "image":
                    im = m.get("image") or {}
                    item["media_id"] = im.get("id")
                    item["mime"] = im.get("mime_type") or "image/jpeg"
                    item["text"] = (im.get("caption") or "").strip()
                elif mtype == "document":
                    doc = m.get("document") or {}
                    item["media_id"] = doc.get("id")
                    item["mime"] = doc.get("mime_type") or ""
                    item["text"] = (doc.get("caption") or "").strip()
                elif mtype == "interactive":
                    inter = m.get("interactive") or {}
                    btn = inter.get("button_reply") or inter.get("list_reply") or {}
                    item["text"] = (btn.get("title") or "").strip()
                out.append(item)
    return out


# ==========================================================================
# Cloud API calls
# ==========================================================================
async def download_media(media_id: str, mime_hint: str = ""):
    """Resolve a media id to (bytes, mime, filename)."""
    token = _token()
    if not token:
        raise RuntimeError("WhatsApp token is not configured.")
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=90.0) as c:
        info_res = await c.get(f"{GRAPH}/{media_id}", headers=headers)
        info_res.raise_for_status()
        info = info_res.json()
        url = info.get("url")
        mime = (info.get("mime_type") or mime_hint or "").split(";")[0].strip()
        if not url:
            raise RuntimeError("No media URL returned by WhatsApp.")
        media_res = await c.get(url, headers=headers)
        media_res.raise_for_status()
        data = media_res.content
    ext = _EXT_BY_MIME.get(mime, "bin")
    return data, mime, f"wa-media.{ext}"


async def send_text(phone_number_id: str, to: str, body: str) -> None:
    """Send a plain-text WhatsApp reply."""
    token = _token()
    pnid = phone_number_id or _default_phone_id()
    if not token or not pnid or not to or not body:
        print("WhatsApp send skipped (missing token / phone id / recipient / body).")
        return
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body[:4090]},  # WhatsApp caps at 4096
    }
    async with httpx.AsyncClient(timeout=30.0) as c:
        res = await c.post(f"{GRAPH}/{pnid}/messages", json=payload,
                           headers={"Authorization": f"Bearer {token}",
                                    "Content-Type": "application/json"})
    if res.status_code >= 400:
        print(f"WhatsApp send failed {res.status_code}: {res.text[:300]}")


# ==========================================================================
# identity + de-duplication
# ==========================================================================
def _db():
    from app.db.supabase import get_supabase_client
    return get_supabase_client().client


def _claim_event(wamid: str) -> bool:
    """Insert the message id; False if we've already handled it (Meta retry)."""
    if not wamid:
        return False
    try:
        _db().table("biz_wa_events").insert({"wamid": wamid}).execute()
        return True
    except Exception:
        return False


def _get_or_create_user(phone: str, name: str = "") -> dict:
    """WhatsApp gives the number without '+'; we store it E.164 with '+'."""
    db = _db()
    normalised = phone if phone.startswith("+") else "+" + phone
    for candidate in (normalised, phone):
        res = db.table("biz_users").select("*").eq("phone_number", candidate).limit(1).execute()
        if res.data:
            return res.data[0]
    return db.table("biz_users").insert({
        "phone_number": normalised,
        "name": (name or "").strip() or None,
    }).execute().data[0]


# ==========================================================================
# background processing
# ==========================================================================
async def handle_messages(msgs: list) -> None:
    from app.business.routes import _process_message

    for m in msgs:
        sender = m.get("from")
        try:
            if not sender or not _claim_event(m.get("wamid")):
                continue  # duplicate delivery or unusable payload
            user = _get_or_create_user(sender, m.get("name"))

            data = filename = mime = None
            if m.get("media_id"):
                try:
                    data, mime, filename = await download_media(m["media_id"], m.get("mime"))
                except Exception as e:  # noqa: BLE001
                    print(f"WhatsApp media download failed: {e}")
                    await send_text(m.get("phone_number_id"), sender,
                                    "I couldn't open that attachment. Please try sending it again.")
                    continue

            result = await _process_message(user, text=m.get("text", ""),
                                            data=data, filename=filename, mime=mime)
            reply = (result or {}).get("reply") or ""
            if reply:
                await send_text(m.get("phone_number_id"), sender, reply)
        except Exception as e:  # noqa: BLE001 — never let one message kill the batch
            print(f"WhatsApp handling error for {sender}: {type(e).__name__}: {e}")
            try:
                await send_text(m.get("phone_number_id"), sender,
                                "Sorry, something went wrong on my side. Please try again.")
            except Exception:
                pass


# ==========================================================================
# routes
# ==========================================================================
@router.get("/webhook")
async def verify(request: Request):
    """Meta's verification handshake — echo hub.challenge when the token matches."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge") or ""
    expected = _verify_token()
    if mode == "subscribe" and expected and token == expected:
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def receive(request: Request, background: BackgroundTasks):
    """Acknowledge immediately, then process in the background (Meta retries
    anything not answered fast, which would double-record)."""
    try:
        payload = await request.json()
    except Exception:
        return {"status": "ignored"}

    msgs = parse_webhook(payload)
    if msgs:
        background.add_task(handle_messages, msgs)
    return {"status": "ok", "received": len(msgs)}
