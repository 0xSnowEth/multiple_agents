# tools/whatsapp.py
# Send WhatsApp messages via Meta Business API.
# Hub calls this ONLY after Rafi has explicitly confirmed.
# This tool is NEVER available to spokes — hub only.
import os
import logging
import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

META_API_BASE = "https://graph.facebook.com/v19.0"


class SendMessageInput(BaseModel):
    to_number: str          # recipient with country code e.g. "+96512345678"
    message: str            # text to send
    from_number_id: str     # "rafi_primary" | "rafi_billing"


class SendMessageResult(BaseModel):
    success: bool
    message_id: str | None = None
    error: str | None = None


def _resolve_phone_id(from_number_id: str) -> str | None:
    """Map logical number name to Meta phone_number_id from env."""
    if from_number_id == "rafi_primary":
        return os.getenv("META_PRIMARY_PHONE_ID")
    elif from_number_id == "rafi_billing":
        return os.getenv("META_BILLING_PHONE_ID")
    else:
        # Allow passing a raw phone_number_id directly
        return from_number_id


async def send_whatsapp_message(
    to_number: str,
    message: str,
    from_number_id: str,
    buttons: list[dict] | None = None,
    list_action: dict | None = None,
) -> bool:
    """
    Send a WhatsApp text message or interactive button menu via Meta Business API.
    Returns True on success, False on failure.
    IMPORTANT: This is irreversible. Hub must not retry automatically.
    Called only after Rafi's explicit confirmation.
    """
    access_token = os.getenv("META_ACCESS_TOKEN")
    if not access_token:
        logger.error("META_ACCESS_TOKEN not set. Cannot send WhatsApp message.")
        return False

    phone_number_id = _resolve_phone_id(from_number_id)
    if not phone_number_id:
        logger.error(f"No phone number ID found for '{from_number_id}'. Check META_PRIMARY_PHONE_ID / META_BILLING_PHONE_ID.")
        return False

    if buttons:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_number,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": message},
                "action": {
                    "buttons": buttons
                }
            }
        }
    elif list_action:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_number,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": message},
                "action": list_action
            }
        }
    else:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_number,
            "type": "text",
            "text": {"body": message},
        }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{META_API_BASE}/{phone_number_id}/messages",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if resp.status_code == 200:
                data = resp.json()
                msg_id = data.get("messages", [{}])[0].get("id", "unknown")
                logger.info(f"WhatsApp message sent to {to_number}. Message ID: {msg_id}")
                return True
            else:
                logger.error(
                    f"Meta API error sending to {to_number}: "
                    f"HTTP {resp.status_code} — {resp.text}"
                )
                return False

    except httpx.TimeoutException:
        logger.error(f"Timeout sending WhatsApp message to {to_number}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending WhatsApp message: {e}")
        return False


async def download_whatsapp_media(media_id: str, session_id: str, fallback_ext: str | None = None) -> str | None:
    """
    Download media from WhatsApp CDN and save locally.
    Returns local file path on success, None on failure.
    Used when Rafi sends an image/video along with a caption request.
    """
    access_token = os.getenv("META_ACCESS_TOKEN")
    if not access_token:
        logger.error("META_ACCESS_TOKEN not set. Cannot download media.")
        return None

    media_dir = os.path.join(
        os.path.dirname(__file__), "..", "data", "media", session_id
    )
    os.makedirs(media_dir, exist_ok=True)

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Step 1: Get media URL
            meta_resp = await client.get(
                f"{META_API_BASE}/{media_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            meta_resp.raise_for_status()
            media_url = meta_resp.json().get("url")
            mime_type = meta_resp.json().get("mime_type", "image/jpeg")

            if not media_url:
                logger.error(f"No URL returned for media ID {media_id}")
                return None

            # Step 2: Download the media
            dl_resp = await client.get(
                media_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            dl_resp.raise_for_status()

            # Determine extension from mime type
            ext_map = {
                "image/jpeg": "jpg",
                "image/png": "png",
                "image/webp": "webp",
                "video/mp4": "mp4",
                "video/quicktime": "mov",
                "video/3gpp": "mp4",
            }
            ext = ext_map.get(mime_type, "bin")
            
            # Fallback if mime_type contains "video" but isn't explicitly mapped
            if ext == "bin" and "video" in mime_type.lower():
                ext = "mp4"
            if ext == "bin" and fallback_ext:
                ext = fallback_ext
                
            file_path = os.path.join(media_dir, f"{media_id}.{ext}")

            with open(file_path, "wb") as f:
                f.write(dl_resp.content)

            host_url = os.getenv("HOST_URL", "").rstrip("/")
            public_url = f"{host_url}/media/{session_id}/{media_id}.{ext}"

            logger.info(f"Downloaded media {media_id} to {file_path}. Public URL: {public_url}")
            return public_url

    except Exception as e:
        logger.error(f"Failed to download media {media_id}: {e}")
        return None
