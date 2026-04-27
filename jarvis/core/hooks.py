# core/hooks.py
# Cost guard and session logger hooks.
# Cost guard: logs a warning (and optionally blocks) if a single session gets expensive.
# Session logger: writes session JSON to data/sessions/{session_id}.json after each run.
import os
import json
import logging
from datetime import datetime
from core.state import TaskState

logger = logging.getLogger(__name__)

SESSIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "sessions")


def log_session(state: TaskState) -> None:
    """
    Write session state to data/sessions/{session_id}.json for audit trail.
    Silently skips on failure — never crash the main flow over logging.
    """
    try:
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        path = os.path.join(SESSIONS_DIR, f"{state.session_id}.json")

        session_data = {
            **state.model_dump(),
            "logged_at": datetime.now().isoformat(),
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)

        logger.debug(f"[{state.session_id}] Session logged to {path}")

    except Exception as e:
        logger.warning(f"[{state.session_id}] Failed to log session: {e}")


def check_cost_guard(session_id: str, approx_cost_usd: float, threshold: float) -> None:
    """
    Log a warning if a session exceeds the cost threshold.
    Does not block execution — alerting only at this stage.
    Extend this to send a WhatsApp alert to Rafi if costs spike unexpectedly.
    """
    if approx_cost_usd > threshold:
        logger.warning(
            f"[{session_id}] COST ALERT: Session cost ~${approx_cost_usd:.4f} "
            f"exceeds threshold of ${threshold:.2f}. "
            f"Review session log at data/sessions/{session_id}.json"
        )
