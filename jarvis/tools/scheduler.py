# tools/scheduler.py
# Schedule a future WhatsApp message (e.g. 24-hour follow-up after approval request).
# Hub calls this after approval-spoke returns a reminder_message.
# scheduler_runner.py checks this file every 15 minutes and sends due messages.
import os
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

REMINDERS_FILE = os.path.join(
    os.path.dirname(__file__), "..", "data", "scheduled_reminders.json"
)


def _load_reminders() -> list[dict]:
    """Load all scheduled reminders. Returns empty list if file missing."""
    if not os.path.exists(REMINDERS_FILE):
        return []
    try:
        with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load reminders: {e}")
        return []


def _save_reminders(reminders: list[dict]) -> bool:
    """Overwrite the reminders file atomically."""
    try:
        os.makedirs(os.path.dirname(REMINDERS_FILE), exist_ok=True)
        tmp = REMINDERS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(reminders, f, ensure_ascii=False, indent=2)
        os.replace(tmp, REMINDERS_FILE)
        return True
    except Exception as e:
        logger.error(f"Failed to save reminders: {e}")
        return False


async def schedule_reminder(
    to_number: str,
    message: str,
    from_number_id: str,
    delay_hours: int,
) -> bool:
    """
    Append a new scheduled reminder to data/scheduled_reminders.json.
    Returns True on success, False on failure.
    NOTE: Not idempotent — each call creates a new entry.
    """
    try:
        send_at = (datetime.now() + timedelta(hours=delay_hours)).isoformat()
        entry = {
            "to_number": to_number,
            "message": message,
            "from_number_id": from_number_id,
            "send_at": send_at,
            "created_at": datetime.now().isoformat(),
            "sent": False,
        }

        reminders = _load_reminders()
        reminders.append(entry)
        success = _save_reminders(reminders)

        if success:
            logger.info(
                f"Scheduled reminder to {to_number} for {send_at} "
                f"(+{delay_hours}h from now)"
            )
        return success

    except Exception as e:
        logger.error(f"Failed to schedule reminder: {e}")
        return False


async def get_due_reminders() -> list[dict]:
    """
    Return all reminders whose send_at time has passed and haven't been sent yet.
    Used by scheduler_runner.py.
    """
    now = datetime.now()
    reminders = _load_reminders()
    due = [
        r for r in reminders
        if not r.get("sent", False)
        and datetime.fromisoformat(r["send_at"]) <= now
    ]
    logger.debug(f"Found {len(due)} due reminders out of {len(reminders)} total")
    return due


async def mark_reminder_sent(to_number: str, send_at: str) -> bool:
    """
    Mark a specific reminder as sent so scheduler_runner doesn't re-send it.
    """
    try:
        reminders = _load_reminders()
        for r in reminders:
            if r["to_number"] == to_number and r["send_at"] == send_at:
                r["sent"] = True
                r["sent_at"] = datetime.now().isoformat()
                break
        return _save_reminders(reminders)
    except Exception as e:
        logger.error(f"Failed to mark reminder sent: {e}")
        return False
