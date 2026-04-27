# interfaces/whatsapp.py
# FastAPI webhook handler for Meta Business API (WhatsApp).
# Inbound: receives Rafi's messages, validates sender, runs hub.
# Outbound: sends Jarvis's replies back to Rafi via Meta API.
import os
import json
import logging
import hmac
import hashlib
import httpx
from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks
from core.hub.orchestrator import run_hub
from core.state import TaskState, SessionStore
from core.hooks import log_session
from tools.whatsapp import send_whatsapp_message, download_whatsapp_media
from tools.scheduler import schedule_reminder

logger = logging.getLogger(__name__)

from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Jarvis — WhatsApp Webhook")
store = SessionStore()
oauth_page_cache = {}  # In-memory cache for Meta OAuth pages

media_dir = os.path.join(os.path.dirname(__file__), "..", "data", "media")
os.makedirs(media_dir, exist_ok=True)
app.mount("/media", StaticFiles(directory=media_dir), name="media")


# ── Webhook verification (GET) ────────────────────────────────────────────────

@app.get("/webhook")
async def verify_webhook(request: Request):
    """
    Meta sends a GET challenge to verify the webhook URL.
    Responds with the hub.challenge value if token matches.
    """
    params = dict(request.query_params)
    verify_token = os.getenv("WEBHOOK_VERIFY_TOKEN", "")
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == verify_token:
        logger.info("Webhook verified by Meta.")
        return Response(content=challenge, media_type="text/plain")

    logger.warning(f"Webhook verification failed. Token mismatch or wrong mode: {mode}")
    raise HTTPException(status_code=403, detail="Verification failed")


# ── Inbound messages (POST) ───────────────────────────────────────────────────

@app.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    """
    Meta posts all inbound WhatsApp messages here.
    Validates sender is Rafi, runs the hub, sends reply.
    """
    body = await request.body()

    # Optional: verify Meta signature (recommended for production)
    app_secret = os.getenv("META_APP_SECRET")
    if app_secret:
        sig_header = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_meta_signature(body, app_secret, sig_header):
            logger.warning("Invalid Meta signature — rejecting request.")
            raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.error("Could not parse webhook payload as JSON.")
        return {"status": "ok"}  # Always return 200 to Meta to avoid retries

    messages = _extract_messages(payload)
    if not messages:
        return {"status": "ok"}

    for msg in messages:
        background_tasks.add_task(_handle_message, msg)

    return {"status": "ok"}


async def _handle_message(msg: dict) -> None:
    """Process a single inbound WhatsApp message."""
    sender = msg.get("from", "")
    rafi_number = os.getenv("RAFI_WHATSAPP_NUMBER", "")

    primary = os.getenv("RAFI_WHATSAPP_NUMBER", "").lstrip("+")
    extras_raw = os.getenv("EXTRA_OPERATOR_NUMBERS", "")
    allowed = {primary} | {n.strip().lstrip("+") for n in extras_raw.split(",") if n.strip()}

    if sender not in allowed:
        logger.info(f"Ignored message from unauthorized number: {sender}")
        return

    msg_type = msg.get("type", "text")
    text = ""

    session_id = store.get_or_create_session(sender)
    state = store.load(session_id) or TaskState(
        session_id=session_id,
        operator_number=f"+{sender}",
    )

    # ── Enforce Document uploads ONLY for video ───────────────────────
    if msg_type == "video":
        await _send_to_rafi(f"+{sender}", "⚠️ Please resend videos as *Document* (not from Gallery) so Jarvis gets the original, uncompressed quality for Reels.")
        return

    if msg_type in ("document", "image"):
        import asyncio
        media_obj = msg.get(msg_type, {})
        mime_type = media_obj.get("mime_type", "image/jpeg").lower() if msg_type == "document" else "image/jpeg"
        media_id = media_obj.get("id", "")
        filename = media_obj.get("filename", "")
        caption = media_obj.get("caption", "")

        if not (mime_type.startswith("image/") or mime_type.startswith("video/")):
            await _send_to_rafi(f"+{sender}", "That document is not a supported image or video file.")
            return

        if state.workflow == "preview":
            await _send_to_rafi(f"+{sender}", "A preview is still open. Finish it first (Post Now / Edit) before sending new media.")
            return

        media_kind = "video" if mime_type.startswith("video/") else "image"
        source_text = caption or ""

        if not state.media_batch:
            state.media_batch = []
        state.media_batch.append({"media_id": media_id, "filename": filename, "mime_type": mime_type, "kind": media_kind})
        store.save(state)

        batch_count = len(state.media_batch)
        if media_kind == "video":
            await _send_to_rafi(f"+{sender}", "🎥 *Video received!* Generating Reel caption in ~10 seconds...")
        elif batch_count == 1:
            await _send_to_rafi(f"+{sender}", "📸 *Image received!* Send more within 10 seconds for a Carousel, or wait for the caption.")
        else:
            await _send_to_rafi(f"+{sender}", f"🖼️ *{batch_count} images received!* Carousel confirmed. Send more or wait 10 seconds.")

        await asyncio.sleep(10)

        state = store.load(session_id)
        if state is None or len(state.media_batch or []) > batch_count:
            return  # More media arrived — let that coroutine finalize

        if not state.client_id:
            await _send_to_rafi(f"+{sender}", "⚠️ No client selected. Go to the menu and pick a client first.")
            state.media_batch = []
            store.save(state)
            return

        video_refs = [m for m in state.media_batch if m["kind"] == "video"]
        image_refs = [m for m in state.media_batch if m["kind"] == "image"]
        if len(video_refs) > 1 or (video_refs and image_refs):
            await _send_to_rafi(f"+{sender}", "⚠️ Mixed media not supported. Send either one video OR one or more images.")
            state.media_batch = []
            store.save(state)
            return

        downloaded_urls = []
        for ref in state.media_batch:
            fallback = "mp4" if ref["kind"] == "video" else "jpg"
            url = await download_whatsapp_media(ref["media_id"], session_id, fallback_ext=fallback)
            if not url:
                await _send_to_rafi(f"+{sender}", "⚠️ Failed to download a media file. Please resend.")
                state.media_batch = []
                store.save(state)
                return
            downloaded_urls.append({"url": url, "kind": ref["kind"], "mime_type": ref["mime_type"]})

        if video_refs:
            content_type = "a Reel (video)"
        elif len(downloaded_urls) > 1:
            content_type = f"a Carousel ({len(downloaded_urls)} images)"
        else:
            content_type = "a Single Image post"

        primary_url = downloaded_urls[0]["url"]
        lang_pref = (state.client_profile.language_preference if state.client_profile else "English") or "English"

        hub_text = (
            f"I have uploaded {len(state.media_batch)} media file(s) for client '{state.client_id}'. "
            f"This is {content_type}. Use caption_spoke to generate the caption. "
            f"ONLY generate in {lang_pref} — do NOT add other languages. "
            f"Notes: {source_text or 'none'}. "
            f"Reply with a clean preview: Client, Format, Caption, Hashtags. Do not post yet."
        )

        updated_state = await run_hub(hub_text, state)
        reply = updated_state.pending_reply or "Caption ready."

        spoke_data = updated_state.spoke_result or {}
        lang_lower = lang_pref.lower()
        if "arabic" in lang_lower and "english" not in lang_lower:
            caption_text = spoke_data.get("arabic_caption", "")
            hashtags = spoke_data.get("arabic_hashtags") or []
        elif "english" in lang_lower and "arabic" not in lang_lower:
            caption_text = spoke_data.get("english_caption", "")
            hashtags = spoke_data.get("english_hashtags") or []
        else:
            ar = spoke_data.get("arabic_caption", "")
            en = spoke_data.get("english_caption", "")
            caption_text = f"{ar}\n\n{en}" if ar and en else (ar or en)
            hashtags = (spoke_data.get("arabic_hashtags") or []) + (spoke_data.get("english_hashtags") or [])

        if not caption_text:
            caption_text = reply
        if hashtags:
            caption_text += "\n\n" + " ".join(f"#{h}" for h in hashtags)

        platforms = (updated_state.client_profile.platforms if updated_state.client_profile else ["Instagram"]) or ["Instagram"]
        updated_state.pending_caption = {"caption": caption_text, "platforms": platforms, "image_url": primary_url}
        updated_state.workflow = "preview"
        updated_state.media_batch = []
        store.save(updated_state)

        buttons = [
            {"type": "reply", "reply": {"id": "execute_post_now", "title": "\U0001f680 Post Now"}},
            {"type": "reply", "reply": {"id": "execute_schedule", "title": "\U0001f4c5 Schedule"}},
            {"type": "reply", "reply": {"id": "execute_edit", "title": "\u270d\ufe0f Edit Caption"}},
        ]
        await send_whatsapp_message(f"+{sender}", reply, "rafi_primary", buttons=buttons)
        return

    if msg_type == "text":
        text = msg.get("text", {}).get("body", "")
    elif msg_type == "interactive":
        interactive = msg.get("interactive", {})
        if interactive.get("type") == "button_reply":
            text = interactive.get("button_reply", {}).get("id", "")
        elif interactive.get("type") == "list_reply":
            text = interactive.get("list_reply", {}).get("id", "")
    elif msg_type == "audio":
        text = "[Voice message received — Jarvis cannot process audio yet.]"
    else:
        text = f"[{msg_type} received]"

    if not text.strip():
        return

    # state already loaded above for document handling

    logger.info(f"[{state.session_id}] Message from Rafi: {text[:80]!r}")

    # ── PYTHON STATE ROUTER ───────────────────────────────────────────────────
    text_lower = text.strip().lower()

    if text_lower in ("hey jarvis", "menu", "home", "hi jarvis", "start"):
        buttons = [
            {"type": "reply", "reply": {"id": "menu_post_content", "title": "📸 Post Content"}},
            {"type": "reply", "reply": {"id": "menu_agency_ops", "title": "💼 Agency Ops"}},
            {"type": "reply", "reply": {"id": "menu_add_client", "title": "➕ Add Client"}}
        ]
        await send_whatsapp_message(f"+{sender}", "Jarvis Operations Terminal 🟢\nWhat's the move?", "rafi_primary", buttons=buttons)
        return

    if text.startswith("link_page_"):
        parts = text.replace("link_page_", "").split("_")
        if len(parts) >= 2:
            client_slug = parts[0]
            page_id = parts[1]
            pages = oauth_page_cache.get(client_slug, [])
            selected_page = next((p for p in pages if p["id"] == page_id), None)
            if selected_page:
                page_token = selected_page.get("access_token")
                from tools.client_store import read_client_profile, write_client_profile
                client_data = await read_client_profile(client_slug)
                if "error" not in client_data:
                    client_data["fb_page_id"] = page_id
                    client_data["fb_page_access_token"] = page_token
                    try:
                        async with httpx.AsyncClient(timeout=30.0) as client:
                            ig_resp = await client.get(
                                f"https://graph.facebook.com/v19.0/{page_id}",
                                params={"fields": "instagram_business_account", "access_token": page_token}
                            )
                            ig_data = ig_resp.json()
                            logger.info(f"IG lookup for page {page_id}: {ig_data}")
                            if "instagram_business_account" in ig_data:
                                client_data["ig_account_id"] = ig_data["instagram_business_account"]["id"]
                    except Exception as e:
                        logger.error(f"Failed to fetch Instagram account for page {page_id}: {e}", exc_info=True)
                    from core.state import ClientProfile
                    await write_client_profile(ClientProfile(**client_data))
                    
                    if client_data.get("ig_account_id"):
                        await _send_to_rafi(f"+{sender}", f"✅ Meta Accounts (Facebook & Instagram) successfully linked to {client_slug}!")
                    else:
                        await _send_to_rafi(f"+{sender}", f"✅ Facebook Page successfully linked to {client_slug}!\n\n⚠️ *WARNING*: No Instagram Business Account was found linked to this Facebook Page. You will not be able to post to Instagram until you link an Instagram Professional account to this Facebook Page in your Meta Business Suite settings.")
                else:
                    await _send_to_rafi(f"+{sender}", "⚠️ Client profile not found.")
            else:
                await _send_to_rafi(f"+{sender}", "⚠️ Auth session expired. Please click the link again.")
        return

    if text == "menu_add_client":
        template = (
            "Please copy, fill out, and send back this exact template:\n\n"
            "Client Name: \n"
            "Brand Voice: \n"
            "Target Audience: \n"
            "Platforms: \n"
            "Language: \n"
            "Number: "
        )
        await _send_to_rafi(f"+{sender}", template)
        return

    if text == "execute_post_now":
        if not state.pending_caption:
            await _send_to_rafi(f"+{sender}", "⚠️ No caption found. Generate a preview first.")
            return
        client_id = state.client_id
        caption = state.pending_caption.get("caption", "")
        image_url = state.pending_caption.get("image_url")
        platforms = state.pending_caption.get("platforms", ["Instagram"])
        await _send_to_rafi(f"+{sender}", f"🚀 Executing post for {client_id}...")
        logger.info(f"Post Now: client={client_id} platforms={platforms} url={image_url}")
        from tools.meta_posting import publish_post
        from tools.client_store import read_client_profile
        client_data = await read_client_profile(client_id or "")
        if "error" in client_data:
            await _send_to_rafi(f"+{sender}", f"⚠️ Client '{client_id}' not found.")
            return
        results = await publish_post(client_profile=client_data, caption=caption, platforms=platforms, image_url=image_url)
        lines = []
        for r in results:
            if r.get("success"):
                lines.append(f"✅ {r['platform'].title()}: {r.get('post_url', 'Published!')}")
            else:
                lines.append(f"⚠️ {r['platform'].title()} failed: {r.get('error', 'unknown')}")
        await _send_to_rafi(f"+{sender}", "\n".join(lines) or "No posts executed.")
        state.pending_caption = None
        state.workflow = None
        store.save(state)
        return

    if text == "execute_edit":
        state.pending_caption = None
        store.save(state)
        await _send_to_rafi(f"+{sender}", "Type your changes (e.g. 'Make it shorter' or 'Add emojis'):")
        return

    if text == "execute_schedule":
        await _send_to_rafi(f"+{sender}", "[Phase 2 Feature] Scheduling not yet linked to cron. Use 'Post Now'.")
        return

    if text == "menu_agency_ops":
        list_action = {
            "button": "View Options",
            "sections": [{
                "title": "Agency Operations",
                "rows": [
                    {"id": "ops_approval", "title": "📝 Get Approval"},
                    {"id": "ops_invoice", "title": "💰 Chase Invoice"},
                    {"id": "ops_strategy", "title": "🎯 Campaign Strategy"},
                    {"id": "ops_config_client", "title": "⚙️ Configure Client"}
                ]
            }]
        }
        await send_whatsapp_message(f"+{sender}", "Select an operation:", "rafi_primary", list_action=list_action)
        return

    if text == "ops_config_client":
        from tools.client_store import list_clients
        clients = await list_clients()
        if not clients:
            await _send_to_rafi(f"+{sender}", "No clients found.")
            return
        rows = []
        for c in clients[:10]:
            rows.append({"id": f"reauth_client_{c['id']}", "title": c['name'][:24]})
        list_action = {
            "button": "Select Client",
            "sections": [{"title": "Saved Clients", "rows": rows}]
        }
        await send_whatsapp_message(f"+{sender}", "Select a client to refresh their Meta tokens:", "rafi_primary", list_action=list_action)
        return

    if text.startswith("reauth_client_"):
        client_slug = text.replace("reauth_client_", "")
        host_url = os.getenv("HOST_URL", "https://<your-ngrok>.ngrok-free.app")
        await _send_to_rafi(
            f"+{sender}",
            f"🔗 Click here to refresh Meta Tokens for {client_slug}:\n{host_url}/api/auth/meta?client_id={client_slug}"
        )
        return

    if text == "menu_post_content":
        from tools.client_store import list_clients
        clients = await list_clients()
        if not clients:
            await _send_to_rafi(f"+{sender}", "No clients found. Click '➕ Add Client' first.")
            return
        
        buttons = []
        for c in clients[:3]:
            buttons.append({"type": "reply", "reply": {"id": f"post_client_{c['id']}", "title": c['name'][:20]}})
        
        await send_whatsapp_message(f"+{sender}", "Select a client to post for:", "rafi_primary", buttons=buttons)
        return

    if text.startswith("post_client_"):
        client_id = text.replace("post_client_", "")
        state.workflow = "posting"
        state.client_id = client_id
        state.media_batch = [] # Reset the batch when selecting a client
        store.save(state)
        await _send_to_rafi(f"+{sender}", f"Ready to draft a post for {client_id}.\n\nPlease upload the image/video as a Document to preserve quality.")
        return

    if state.workflow == "posting" and media_path:
        state.media_batch.append({"type": actual_type, "path": media_path})
        store.save(state)
        current_batch_size = len(state.media_batch)
        
        if current_batch_size == 1:
            if actual_type == "video":
                await _send_to_rafi(f"+{sender}", "🎥 *Video Received!*\n\nI've classified this as a Reel. Sit tight while I generate the captions and format the preview.")
            else:
                await _send_to_rafi(f"+{sender}", "📸 *Image Received!*\n\nI've set this up as a Single Image post. If you have more images, just drop them now to instantly upgrade it to a Carousel.")
        elif current_batch_size == 2:
            await _send_to_rafi(f"+{sender}", "🔥 *2nd Image Received!*\n\nI've automatically upgraded this post to a Carousel. Keep dropping them if you have more.")
        else:
            await _send_to_rafi(f"+{sender}", f"📦 *{current_batch_size}th Image Received!*\n\nGot it locked in. I'll compile all of these into your Carousel as soon as you're done uploading.")
            
        import asyncio
        await asyncio.sleep(10)
        
        state = store.load(session_id)
        if len(state.media_batch) > current_batch_size:
            return # Another media arrived, let that thread handle it
            
        media_types = [m["type"] for m in state.media_batch]
        if "video" in media_types:
            content_type = "a Reel"
        elif len(state.media_batch) > 1:
            content_type = "a Carousel"
        else:
            content_type = "a Single Image"
            
        text = f"I have uploaded {len(state.media_batch)} media files for my client '{state.client_id}'. This is {content_type}. First, strictly use the caption_spoke to generate the caption based on their profile. Then, reply to me with a beautiful, professional preview. Include the Client Name, the Media Type, and cleanly present the generated caption with hashtags. STRICTLY respect the client's language preference ({state.client_profile.language_preference if state.client_profile else 'English'}). If they only asked for one language, DO NOT include translations or other languages in your preview. Do not execute the post yet."
        media_path = state.media_batch[0]["path"]
        # Fall through to wake up the Hub LLM

    # Run the hub (if no router rule matched)
    updated_state = await run_hub(text, state)
    
    reply = updated_state.pending_reply or "Got it."

    # ── POST-HUB ROUTER (Format Buttons) ──
    # ── POST-HUB ROUTER (Format Buttons) ──
    # Only show execution buttons if the Hub actually succeeded (didn't overload)
    if updated_state.workflow == "posting" and not getattr(updated_state, 'pending_caption', None) and "overloaded" not in reply.lower() and "error" not in reply.lower():
        # Extract the pure caption to post to social media (not the conversational preview)
        spoke_data = updated_state.spoke_result or {}
        clean_caption = ""
        
        if spoke_data.get("english_caption") and spoke_data.get("arabic_caption"):
            clean_caption = f"{spoke_data['arabic_caption']}\n\n{spoke_data['english_caption']}"
        else:
            clean_caption = spoke_data.get("english_caption") or spoke_data.get("arabic_caption") or reply
            
        hashtags = (spoke_data.get("english_hashtags") or []) + (spoke_data.get("arabic_hashtags") or [])
        if hashtags:
            clean_caption += "\n\n" + " ".join(f"#{h}" for h in hashtags)

        # Save the pure caption into state so the execute buttons can use it
        updated_state.pending_caption = {
            "caption": clean_caption,
            "platforms": updated_state.client_profile.platforms if updated_state.client_profile else ["instagram"],
            "image_url": media_path
        }
        store.save(updated_state)

        buttons = [
            {"type": "reply", "reply": {"id": "execute_post_now", "title": "🚀 Post Now"}},
            {"type": "reply", "reply": {"id": "execute_schedule", "title": "🗓️ Schedule"}},
            {"type": "reply", "reply": {"id": "execute_edit", "title": "✍️ Edit Caption"}}
        ]
        await send_whatsapp_message(f"+{sender}", reply, "rafi_primary", buttons=buttons)
    else:
        store.save(updated_state)
        await _send_to_rafi(f"+{sender}", reply)

    # If there's a confirmed pending action (hub set status=done), execute it
    if updated_state.status == "done" and updated_state.pending_action:
        await _execute_pending_action(updated_state)

    # Log session for audit trail
    log_session(updated_state)

    # Reset session if done
    if updated_state.status == "done":
        store.clear(session_id)


async def _execute_pending_action(state: TaskState) -> None:
    """
    Execute a pending action (send WhatsApp message to client) after Rafi confirms.
    Hub sets status='done' and pending_action when Rafi says "send it" / "yes" / "go ahead".
    """
    action = state.pending_action
    if not action:
        return

    logger.info(
        f"[{state.session_id}] Executing {action.action_type} → {action.recipient_number}"
    )

    if action.action_type == "post_to_page":
        # Execute social media post via Meta Graph API
        from tools.meta_posting import publish_post
        from tools.client_store import read_client_profile
        client_data = await read_client_profile(action.post_client_id or "")
        if "error" in client_data:
            await _send_to_rafi(state.operator_number, f"⚠️ Couldn't load client profile: {client_data['error']}")
            return

        # posting-spoke stored the full plan in state.spoke_result — use it
        posts = (state.spoke_result or {}).get("posts", [])
        ready_posts = [p for p in posts if p.get("ready")]

        results = []
        if ready_posts:
            for post in ready_posts:
                res = await publish_post(
                    client_profile=client_data,
                    caption=post["caption"],
                    platforms=[post["platform"]],
                    image_url=post.get("image_url"),
                )
                results.extend(res)
        else:
            # Direct post from Hub (fallback)
            res = await publish_post(
                client_profile=client_data,
                caption=action.message,
                platforms=action.post_platforms or ["facebook", "instagram"], # Default to both if not specified
                image_url=action.post_image_url,
            )
            results.extend(res)

        successes = [r for r in results if r.get("success")]
        failures = [r for r in results if not r.get("success")]

        reply_lines = []
        for r in successes:
            reply_lines.append(f"✅ Posted to {r['platform'].title()}: {r.get('post_url', '')}")
        for r in failures:
            reply_lines.append(f"⚠️ {r['platform'].title()} failed: {r.get('error', 'unknown error')}")

        await _send_to_rafi(state.operator_number, "\n".join(reply_lines) or "No posts were executed.")
        return

    # WhatsApp message send (all non-posting actions)
    sent = await send_whatsapp_message(
        to_number=action.recipient_number,
        message=action.message,
        from_number_id=action.from_number,
    )

    if sent:
        logger.info(f"[{state.session_id}] Message sent to {action.recipient_number}")
        if action.follow_up_message:
            await schedule_reminder(
                to_number=action.recipient_number,
                message=action.follow_up_message,
                from_number_id=action.from_number,
                delay_hours=action.follow_up_delay_hours,
            )
    else:
        logger.error(f"[{state.session_id}] Failed to send message to {action.recipient_number}")
        await _send_to_rafi(
            state.operator_number,
            f"⚠️ Couldn't send the message to {action.recipient_number}. "
            "Check your Meta API token and try again."
        )


async def _send_to_rafi(rafi_number: str, message: str) -> None:
    """Send Jarvis's reply to Rafi. Splits messages >1600 chars for WhatsApp."""
    chunks = _chunk_message(message, max_length=1600)
    for chunk in chunks:
        success = await send_whatsapp_message(
            to_number=rafi_number,
            message=chunk,
            from_number_id="rafi_primary",
        )
        if not success:
            logger.error(f"Failed to send reply to Rafi at {rafi_number}")
            break


def _chunk_message(text: str, max_length: int = 1600) -> list[str]:
    """Split long text into WhatsApp-safe chunks at paragraph boundaries."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    paragraphs = text.split("\n\n")
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_length:
            current = f"{current}\n\n{para}".lstrip("\n")
        else:
            if current:
                chunks.append(current.strip())
            current = para

    if current.strip():
        chunks.append(current.strip())

    return chunks or [text[:max_length]]


def _extract_messages(payload: dict) -> list[dict]:
    """Extract message objects from Meta webhook payload."""
    messages = []
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    messages.append(msg)
    except Exception as e:
        logger.warning(f"Error extracting messages from payload: {e}")
    return messages


def _verify_meta_signature(body: bytes, app_secret: str, sig_header: str) -> bool:
    """
    Verify the X-Hub-Signature-256 header from Meta.
    Add META_APP_SECRET to .env to enable this check (recommended for production).
    """
    if not sig_header.startswith("sha256="):
        return False
    expected = hmac.new(
        app_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    provided = sig_header[len("sha256="):]
    return hmac.compare_digest(expected, provided)


# ── Meta OAuth Flows ──────────────────────────────────────────────────────────

from fastapi.responses import RedirectResponse

@app.get("/api/auth/meta")
async def meta_auth_redirect(client_id: str):
    """Redirects the user to Meta's OAuth screen."""
    app_id = os.getenv("META_APP_ID")
    redirect_uri = os.getenv("META_REDIRECT_URI") # e.g. https://xxx.ngrok-free.app/api/meta-oauth-callback
    if not app_id or not redirect_uri:
        return {"error": "Missing META_APP_ID or META_REDIRECT_URI in .env"}

    oauth_url = (
        f"https://www.facebook.com/v19.0/dialog/oauth?"
        f"client_id={app_id}&"
        f"redirect_uri={redirect_uri}&"
        f"state={client_id}&"
        f"scope=pages_manage_posts,pages_read_engagement,instagram_basic,instagram_content_publish"
    )
    return RedirectResponse(oauth_url)


@app.get("/api/meta-oauth-callback")
async def meta_oauth_callback(code: str, state: str):
    """
    Receives code from Meta, trades for User Token, gets Pages,
    and AUTO-LINKS the first page immediately — no second WhatsApp interaction needed.
    """
    client_id = state
    app_id = os.getenv("META_APP_ID")
    app_secret = os.getenv("META_APP_SECRET")
    redirect_uri = os.getenv("META_REDIRECT_URI")
    operator_number = os.getenv("RAFI_WHATSAPP_NUMBER", "").lstrip("+")

    if not operator_number:
        return {"error": "Missing RAFI_WHATSAPP_NUMBER"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Step 1: Exchange code for user token
            token_resp = await client.get(
                f"https://graph.facebook.com/v19.0/oauth/access_token?"
                f"client_id={app_id}&redirect_uri={redirect_uri}&client_secret={app_secret}&code={code}"
            )
            token_data = token_resp.json()
            user_token = token_data.get("access_token")

            if not user_token:
                logger.error(f"OAuth token exchange failed for {client_id}: {token_data}")
                await _send_to_rafi(operator_number, f"⚠️ OAuth failed for {client_id}: Could not get access token from Meta.")
                return {"error": "Failed to get user token", "details": token_data}

            # Step 2: Get all pages this user manages
            pages_resp = await client.get(
                f"https://graph.facebook.com/v19.0/me/accounts?access_token={user_token}"
            )
            pages_data = pages_resp.json().get("data", [])

            if not pages_data:
                logger.error(f"No Facebook Pages found for {client_id}")
                await _send_to_rafi(operator_number, f"⚠️ No Facebook Pages found for your account. Make sure you manage at least one Page.")
                return {"error": "No Facebook Pages found for this user."}

            # Step 3: Auto-link the first page (no WhatsApp list needed)
            selected_page = pages_data[0]
            page_id = selected_page["id"]
            page_name = selected_page.get("name", "Unknown Page")
            page_token = selected_page.get("access_token")

            logger.info(f"OAuth: Auto-linking page '{page_name}' (ID: {page_id}) to client {client_id}")

            # Step 4: Load client profile
            from tools.client_store import read_client_profile, write_client_profile
            client_data = await read_client_profile(client_id)
            if "error" in client_data:
                logger.error(f"Client profile not found during OAuth: {client_id}")
                await _send_to_rafi(operator_number, f"⚠️ Client '{client_id}' not found in database. Add the client first.")
                return {"error": f"Client '{client_id}' not found"}

            # Step 5: Save Facebook credentials
            client_data["fb_page_id"] = page_id
            client_data["fb_page_access_token"] = page_token

            # Step 6: Fetch Instagram Business Account linked to this page
            ig_account_id = None
            try:
                ig_resp = await client.get(
                    f"https://graph.facebook.com/v19.0/{page_id}",
                    params={"fields": "instagram_business_account", "access_token": page_token}
                )
                ig_data = ig_resp.json()
                logger.info(f"Instagram lookup response for page {page_id}: {ig_data}")

                if "instagram_business_account" in ig_data:
                    ig_account_id = ig_data["instagram_business_account"]["id"]
                    client_data["ig_account_id"] = ig_account_id
                    logger.info(f"Found Instagram Business Account: {ig_account_id}")
                else:
                    logger.warning(f"No Instagram Business Account linked to page {page_id}. Response: {ig_data}")
            except Exception as e:
                logger.error(f"Failed to fetch Instagram account for page {page_id}: {e}")

            # Step 7: Write updated profile to disk
            from core.state import ClientProfile
            profile = ClientProfile(**client_data)
            write_result = await write_client_profile(profile.model_dump())
            if not write_result:
                logger.error(f"Failed to write client profile for {client_id}")
                await _send_to_rafi(operator_number, f"⚠️ Failed to save Meta credentials for {client_id}. Check server logs.")
                return {"error": "Failed to save client profile"}

            # Step 8: Notify operator with results
            status_lines = [
                f"✅ *Meta Accounts Linked for {client_id}*",
                f"",
                f"📘 *Facebook Page:* {page_name}",
                f"🔑 *Page ID:* {page_id}",
                f"🎟️ *Page Token:* ✅ Saved",
            ]
            if ig_account_id:
                status_lines.append(f"📸 *Instagram ID:* {ig_account_id} ✅")
            else:
                status_lines.append(f"📸 *Instagram:* ⚠️ Not linked to this Facebook Page")
                status_lines.append(f"")
                status_lines.append(f"To fix: Go to Meta Business Suite → Settings → Linked Accounts → Connect your Instagram Professional account to this Page.")

            await _send_to_rafi(operator_number, "\n".join(status_lines))

        return {"status": "success", "message": f"Linked page '{page_name}' to {client_id}. Check WhatsApp for confirmation!"}

    except Exception as e:
        logger.error(f"OAuth callback crashed for {client_id}: {e}", exc_info=True)
        await _send_to_rafi(operator_number, f"⚠️ OAuth failed for {client_id}: {str(e)}")
        return {"error": str(e)}


async def start_webhook():
    """Start the FastAPI webhook server via uvicorn."""
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    log_level = os.getenv("LOG_LEVEL", "info").lower()
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level=log_level)
    server = uvicorn.Server(config)
    logger.info(f"Starting Jarvis webhook on port {port}")
    await server.serve()
