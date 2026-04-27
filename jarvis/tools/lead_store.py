# tools/lead_store.py
# Append-log of all leads. Used by lead-qualification-spoke.
# Storage: data/leads.json (append-only list)
import os
import json
import logging
from datetime import datetime
from core.state import LeadRecord

logger = logging.getLogger(__name__)

LEADS_FILE = os.path.join(
    os.path.dirname(__file__), "..", "data", "leads.json"
)


def _load_leads() -> list[dict]:
    if not os.path.exists(LEADS_FILE):
        return []
    try:
        with open(LEADS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load leads: {e}")
        return []


async def create_lead_record(
    phone: str,
    qualification_level: str,
    summary: str,
    recommended_action: str,
    key_signals: list[str],
) -> bool:
    """
    Append a new lead record to data/leads.json.
    Returns True on success, False on failure.
    Same phone can have multiple records — timestamps differentiate them.
    """
    try:
        record = LeadRecord(
            phone=phone,
            initial_message="",  # Not stored here — already in session log
            qualification_level=qualification_level,
            summary=summary,
            recommended_action=recommended_action,
            key_signals=key_signals,
        )

        leads = _load_leads()
        leads.append(record.model_dump())

        os.makedirs(os.path.dirname(LEADS_FILE), exist_ok=True)
        tmp = LEADS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(leads, f, ensure_ascii=False, indent=2)
        os.replace(tmp, LEADS_FILE)

        logger.info(
            f"Lead record created: {phone} | {qualification_level} | {recommended_action}"
        )
        return True

    except Exception as e:
        logger.error(f"Failed to create lead record for {phone}: {e}")
        return False
