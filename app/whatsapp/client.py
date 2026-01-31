"""
WhatsApp Cloud API client.

Handles sending messages via the WhatsApp Cloud API.
"""
import os
from typing import Optional

import httpx


class WhatsAppClient:
    """Client for WhatsApp Cloud API."""
    
    BASE_URL = "https://graph.facebook.com/v18.0"
    
    def __init__(self):
        self.token = os.getenv("WHATSAPP_TOKEN")
        self.phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        
        if not self.token:
            print("Warning: WHATSAPP_TOKEN not set")
        if not self.phone_number_id:
            print("Warning: WHATSAPP_PHONE_NUMBER_ID not set")
    
    async def send_message(
        self, 
        to: str, 
        text: str,
        preview_url: bool = False,
    ) -> Optional[dict]:
        """
        Send a text message via WhatsApp.
        
        Args:
            to: Recipient phone number (with country code, no +)
            text: Message text
            preview_url: Whether to show URL previews
        
        Returns:
            API response dict or None on error
        """
        if not self.token or not self.phone_number_id:
            print("WhatsApp client not configured, skipping send")
            return None
        
        url = f"{self.BASE_URL}/{self.phone_number_id}/messages"
        
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {
                "preview_url": preview_url,
                "body": text,
            }
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url, 
                    headers=headers, 
                    json=payload,
                    timeout=30.0,
                )
                response.raise_for_status()
                return response.json()
                
        except httpx.HTTPError as e:
            print(f"WhatsApp API error: {e}")
            return None
    
    async def send_template(
        self,
        to: str,
        template_name: str,
        language_code: str = "en",
        components: Optional[list] = None,
    ) -> Optional[dict]:
        """
        Send a template message via WhatsApp.
        
        Useful for initiating conversations or sending structured messages.
        """
        if not self.token or not self.phone_number_id:
            return None
        
        url = f"{self.BASE_URL}/{self.phone_number_id}/messages"
        
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
            }
        }
        
        if components:
            payload["template"]["components"] = components
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=30.0,
                )
                response.raise_for_status()
                return response.json()
                
        except httpx.HTTPError as e:
            print(f"WhatsApp template error: {e}")
            return None
    
    async def mark_as_read(self, message_id: str) -> bool:
        """Mark a message as read."""
        if not self.token or not self.phone_number_id:
            return False
        
        url = f"{self.BASE_URL}/{self.phone_number_id}/messages"
        
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=10.0,
                )
                return response.is_success
                
        except httpx.HTTPError:
            return False
