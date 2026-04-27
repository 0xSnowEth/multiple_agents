# tools/meta_posting.py
# Post content to Facebook Pages and Instagram Business Accounts
# via Meta Graph API using per-client Page Access Tokens.
#
# HOW TOKENS WORK:
# - During demo (Triliva app): Page Access Token is stored in each client profile JSON.
#   Triliva already has pages_manage_posts permission — no extra setup needed.
# - After client onboards their own Meta Business account: same flow, just update
#   fb_page_id, fb_page_access_token, and ig_account_id in their profile JSON.
# - Page Access Tokens last ~60 days for long-lived tokens.
#   Refresh via Triliva app or your own Meta app's token refresh endpoint.
#
# INSTAGRAM IMAGE REQUIREMENT:
# - Instagram graph API requires a publicly accessible image URL.
# - WhatsApp-downloaded media is local and cannot be used directly.
# - Options: Rafi provides a public URL, or upload to Cloudinary first (future enhancement).
# - Facebook feed posts work with or without an image.
import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

META_GRAPH_BASE = "https://graph.facebook.com/v19.0"


# ── Facebook Page Posting ─────────────────────────────────────────────────────

async def post_to_facebook_page(
    page_id: str,
    page_access_token: str,
    message: str,
    image_url: Optional[str] = None,
    link_url: Optional[str] = None,
) -> dict:
    """
    Post to a Facebook Page feed.
    Supports text-only, text + image, or text + link.
    Returns {"success": True, "post_id": "...", "post_url": "..."} on success.
    Returns {"success": False, "error": "..."} on failure.
    Never raises — caller decides how to handle failures.
    """
    endpoint = f"{META_GRAPH_BASE}/{page_id}"
    params = {"access_token": page_access_token}

    if image_url:
        # Photo post — posts to /photos endpoint, appears in feed
        url = f"{endpoint}/photos"
        payload = {
            "url": image_url,
            "caption": message,
        }
    elif link_url:
        # Link post
        url = f"{endpoint}/feed"
        payload = {
            "message": message,
            "link": link_url,
        }
    else:
        # Text-only post
        url = f"{endpoint}/feed"
        payload = {"message": message}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, params=params, json=payload)
            data = resp.json()

            if resp.status_code == 200 and ("id" in data or "post_id" in data):
                post_id = data.get("post_id") or data.get("id")
                post_url = f"https://www.facebook.com/{post_id}"
                logger.info(f"Facebook post published: {post_url}")
                return {
                    "success": True,
                    "post_id": post_id,
                    "post_url": post_url,
                    "platform": "facebook",
                }
            else:
                error_msg = data.get("error", {}).get("message", str(data))
                logger.error(f"Facebook post failed: {error_msg}")
                return {"success": False, "error": error_msg, "platform": "facebook"}

    except httpx.TimeoutException:
        logger.error(f"Timeout posting to Facebook page {page_id}")
        return {"success": False, "error": "Request timed out", "platform": "facebook"}
    except Exception as e:
        logger.error(f"Unexpected error posting to Facebook: {e}")
        return {"success": False, "error": str(e), "platform": "facebook"}


# ── Instagram Business Account Posting ───────────────────────────────────────

async def post_to_instagram(
    ig_account_id: str,
    page_access_token: str,
    caption: str,
    image_url: str,  # REQUIRED for Instagram — must be publicly accessible URL
) -> dict:
    """
    Post an image with caption to Instagram Business Account.
    Two-step process required by Meta Graph API:
    Step 1: Create a media container (returns creation_id)
    Step 2: Publish the container (makes it live)
    Returns {"success": True, "post_id": "...", "post_url": "..."} on success.
    Returns {"success": False, "error": "..."} on failure.

    IMPORTANT: image_url must be:
    - Publicly accessible (not a local file, not a WhatsApp CDN URL)
    - JPEG, PNG, or GIF format
    - Min 320px, max 1440px wide
    - Under 8MB
    """
    params = {"access_token": page_access_token}

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:

            # Step 1: Create media container
            is_video = image_url.lower().endswith(('.mp4', '.mov'))
            
            payload = {"caption": caption}
            if is_video:
                payload["video_url"] = image_url
                payload["media_type"] = "REELS"
            else:
                payload["image_url"] = image_url

            container_resp = await client.post(
                f"{META_GRAPH_BASE}/{ig_account_id}/media",
                params=params,
                json=payload,
            )
            container_data = container_resp.json()

            if container_resp.status_code != 200 or "id" not in container_data:
                error_msg = container_data.get("error", {}).get("message", str(container_data))
                logger.error(f"Instagram container creation failed: {error_msg}")
                return {"success": False, "error": f"Container step failed: {error_msg}", "platform": "instagram"}

            creation_id = container_data["id"]
            logger.debug(f"Instagram container created: {creation_id}")

            # Polling for async video processing
            if is_video:
                import asyncio
                logger.info(f"Polling status for video container {creation_id}...")
                for attempt in range(12):
                    await asyncio.sleep(5)
                    status_resp = await client.get(
                        f"{META_GRAPH_BASE}/{creation_id}",
                        params={"fields": "status_code", "access_token": page_access_token}
                    )
                    status_data = status_resp.json()
                    status = status_data.get("status_code")
                    logger.debug(f"Video container {creation_id} status: {status}")
                    if status == "FINISHED":
                        break
                    elif status == "ERROR":
                        return {"success": False, "error": f"Instagram video processing failed.", "platform": "instagram"}
                else:
                    return {"success": False, "error": "Instagram video processing timed out after 60 seconds.", "platform": "instagram"}

            # Step 2: Publish the container
            publish_resp = await client.post(
                f"{META_GRAPH_BASE}/{ig_account_id}/media_publish",
                params=params,
                json={"creation_id": creation_id},
            )
            publish_data = publish_resp.json()

            if publish_resp.status_code == 200 and "id" in publish_data:
                post_id = publish_data["id"]
                post_url = f"https://www.instagram.com/p/{post_id}/"
                logger.info(f"Instagram post published: {post_id}")
                return {
                    "success": True,
                    "post_id": post_id,
                    "post_url": post_url,
                    "platform": "instagram",
                }
            else:
                error_msg = publish_data.get("error", {}).get("message", str(publish_data))
                logger.error(f"Instagram publish step failed: {error_msg}")
                return {"success": False, "error": f"Publish step failed: {error_msg}", "platform": "instagram"}

    except httpx.TimeoutException:
        logger.error(f"Timeout posting to Instagram account {ig_account_id}")
        return {"success": False, "error": "Request timed out", "platform": "instagram"}
    except Exception as e:
        logger.error(f"Unexpected error posting to Instagram: {e}")
        return {"success": False, "error": str(e), "platform": "instagram"}


# ── Combined Post (Hub calls this) ────────────────────────────────────────────

async def publish_post(
    client_profile: dict,
    caption: str,
    platforms: list[str],
    image_url: Optional[str] = None,
) -> list[dict]:
    """
    Publish to one or both platforms for a given client.
    client_profile must have: fb_page_id, fb_page_access_token, ig_account_id (for Instagram).
    Returns a list of result dicts — one per platform attempted.
    """
    import os
    results = []

    # Fallback to global .env tokens if client-specific ones aren't pasted into their JSON yet
    page_token = client_profile.get("fb_page_access_token") or os.getenv("META_ACCESS_TOKEN")
    if not page_token:
        logger.error(f"No page_access_token for client {client_profile.get('id')}")
        return [{"success": False, "error": "No page access token configured for this client.", "platform": "all"}]

    # Normalize platform names to lowercase for consistent matching
    platforms = [p.lower() for p in platforms]

    if "facebook" in platforms:
        fb_page_id = client_profile.get("fb_page_id") or os.getenv("META_PRIMARY_PAGE_ID")
        if fb_page_id:
            result = await post_to_facebook_page(
                page_id=fb_page_id,
                page_access_token=page_token,
                message=caption,
                image_url=image_url,
            )
            results.append(result)
        else:
            results.append({"success": False, "error": "fb_page_id not set in client profile.", "platform": "facebook"})

    if "instagram" in platforms:
        ig_account_id = client_profile.get("ig_account_id") or os.getenv("META_IG_ACCOUNT_ID")
        if not ig_account_id:
            results.append({"success": False, "error": "ig_account_id not set in client profile.", "platform": "instagram"})
        elif not image_url:
            results.append({
                "success": False,
                "error": (
                    "Instagram requires an image. "
                    "Provide a public image URL (Cloudinary, Dropbox public link, or hosted image). "
                    "WhatsApp-downloaded images can't be used directly — they're not publicly accessible."
                ),
                "platform": "instagram",
            })
        else:
            result = await post_to_instagram(
                ig_account_id=ig_account_id,
                page_access_token=page_token,
                caption=caption,
                image_url=image_url,
            )
            results.append(result)

    return results
