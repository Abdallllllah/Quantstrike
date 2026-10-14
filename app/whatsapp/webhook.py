"""
WhatsApp webhook handler.

Handles incoming webhooks from WhatsApp Cloud API,
validates requests, and routes messages to the orchestrator.
"""
import os
import hashlib
import hmac
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse, JSONResponse

from app.whatsapp.client import WhatsAppClient
from app.whatsapp.formatter import format_for_whatsapp, split_long_message

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])

# Initialize WhatsApp client
_whatsapp_client: Optional[WhatsAppClient] = None


def get_whatsapp_client() -> WhatsAppClient:
    """Get or create WhatsApp client."""
    global _whatsapp_client
    if _whatsapp_client is None:
        _whatsapp_client = WhatsAppClient()
    return _whatsapp_client


@router.get("/webhook")
async def verify_webhook(
    request: Request,
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """
    Webhook verification endpoint for WhatsApp Cloud API.
    
    Meta sends a GET request to verify the webhook URL.
    We must return the hub.challenge if the verify token matches.
    """
    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "your-verify-token")
    
    if hub_mode == "subscribe" and hub_verify_token == verify_token:
        print(f"Webhook verified successfully")
        return PlainTextResponse(content=hub_challenge)
    
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def handle_webhook(request: Request):
    """
    Handle incoming WhatsApp messages.
    
    This is the main entry point for WhatsApp messages.
    """
    try:
        # Parse webhook payload
        payload = await request.json()
        
        # Reject forged webhooks when WHATSAPP_APP_SECRET is configured.
        if os.getenv("WHATSAPP_APP_SECRET") and not validate_signature(
                await request.body(), request.headers.get("X-Hub-Signature-256")):
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        # Extract message data
        message_data = extract_message_from_payload(payload)
        
        if not message_data:
            # Not a message event (could be status update, etc.)
            return JSONResponse({"status": "ok"})
        
        phone_number = message_data["phone_number"]
        message_text = message_data["text"]
        message_id = message_data["message_id"]

        # Same student, allowance and profile as the app (Carati chat service).
        response_text = await answer_whatsapp(phone_number, message_text, message_id)

        client = get_whatsapp_client()
        for part in split_long_message(format_for_whatsapp(response_text)):
            await client.send_message(phone_number, part)

        return JSONResponse({"status": "ok", "message_id": message_id})
        
    except Exception as e:
        print(f"Webhook error: {e}")
        # Still return 200 to acknowledge receipt
        # (WhatsApp will retry on non-200 responses)
        return JSONResponse({"status": "error", "message": str(e)})


def extract_message_from_payload(payload: dict) -> Optional[dict]:
    """
    Extract message details from WhatsApp webhook payload.
    
    WhatsApp Cloud API payload structure:
    {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "phone_number",
                        "text": {"body": "message text"},
                        "id": "message_id"
                    }]
                }
            }]
        }]
    }
    """
    try:
        if payload.get("object") != "whatsapp_business_account":
            return None
        
        entries = payload.get("entry", [])
        if not entries:
            return None
        
        changes = entries[0].get("changes", [])
        if not changes:
            return None
        
        value = changes[0].get("value", {})
        messages = value.get("messages", [])
        
        if not messages:
            return None
        
        message = messages[0]
        
        # Only handle text messages for now
        if message.get("type") != "text":
            return None
        
        return {
            "phone_number": message.get("from"),
            "text": message.get("text", {}).get("body", ""),
            "message_id": message.get("id"),
        }
        
    except (KeyError, IndexError) as e:
        print(f"Error extracting message: {e}")
        return None


def validate_signature(payload: bytes, signature: str) -> bool:
    """
    Validate WhatsApp webhook signature.
    
    Args:
        payload: Raw request body
        signature: X-Hub-Signature-256 header value
    
    Returns:
        True if signature is valid
    """
    if not signature:
        return False
    
    app_secret = os.getenv("WHATSAPP_APP_SECRET", "")
    if not app_secret:
        # Skip validation if no secret configured
        return True
    
    expected = "sha256=" + hmac.new(
        app_secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(signature, expected)


async def answer_whatsapp(raw_phone: str, text: str, wamid: str) -> str:
    """Answer a WhatsApp message through the Carati chat service. One thread per
    student per WAT day, so the follow-up rule works the same as in the app.
    The WhatsApp message id is the idempotency key: Meta's retries are free."""
    import uuid as _uuid
    from app.edu import allowance, chat_service
    from app.edu.db import db, general_school_id, one, run
    from app.edu.phone import normalize_phone

    phone = normalize_phone(raw_phone)
    if not phone:
        return "Sorry, I couldn't read your number."

    def find_or_create():
        u = one(db().table("reg_users").select("*").eq("phone_number", phone))
        return u or db().table("reg_users").insert(
            {"phone_number": phone, "role": "student", "school_id": general_school_id()}).execute().data[0]

    user = await run(find_or_create)
    day = allowance.wat_now().date().isoformat()
    conv = str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"carati:wa:{user['id']}:{day}"))
    try:
        reply = await chat_service.ask(user, text, conversation_id=conv, client_ref=f"wa:{wamid}", channel="whatsapp")
    except chat_service.CapReached:
        p = allowance.cap_payload("question")
        return (p["message"] + " A parent can buy a question pack in the Carati app, "
                "or come back after midnight.")
    left = (await run(allowance.status, user["id"]))["questions"]["left"]
    suffix = f"\n\n({left} free questions left today)" if reply["counted"] else ""
    return reply["answer"] + suffix
