# core/state.py
# Pydantic models for all state objects.
# Hub owns TaskState. Spokes receive scoped subsets via Task prompt — never the full object.
import uuid
import json
import logging
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class ClientProfile(BaseModel):
    id: str                              # slug e.g. "starbucks-kw"
    name: str                            # display name e.g. "Starbucks Kuwait"
    brand_voice: str                     # tone description e.g. "warm, premium, community-first"
    target_audience: str                 # who they're targeting
    platforms: List[str]                 # ["instagram", "facebook"]
    language_preference: str             # "arabic" | "english" | "both"
    whatsapp_number: str                 # client's number for approvals e.g. "+96512345678"
    brand_examples: List[str] = []       # 3-5 example captions for few-shot context
    notes: str = ""                      # special instructions or dos/don'ts

    # ── Meta posting credentials (Triliva demo → client's own account later) ──
    # These are optional — clients without posting setup still work for all other flows.
    fb_page_id: Optional[str] = None             # Facebook Page ID e.g. "123456789"
    fb_page_access_token: Optional[str] = None   # Page Access Token from Triliva/client app
    ig_account_id: Optional[str] = None          # Instagram Business Account ID linked to the page
    # NOTE: Page Access Token lives here (per client) not in .env.
    # When client upgrades to their own Meta Business account, just update these 3 fields.
    # Token rotation: Meta long-lived page tokens last ~60 days — refresh via Triliva app.


class LeadRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    phone: str                           # lead's WhatsApp number
    initial_message: str                 # what they sent
    qualification_level: str             # "HOT" | "WARM" | "COLD"
    summary: str                         # 2-sentence summary from spoke
    recommended_action: str              # "book_call" | "send_portfolio" | "nurture" | "disqualify"
    key_signals: List[str] = []
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class PendingAction(BaseModel):
    action_type: str                     # "send_approval" | "send_payment" | "send_lead_response" | "post_to_page"
    recipient_number: str                # WhatsApp number (messaging) or client_id (posting)
    from_number: str                     # "rafi_primary" | "rafi_billing" | "meta_graph_api"
    message: str                         # exact message to send (or caption for posts)
    follow_up_message: Optional[str] = None
    follow_up_delay_hours: int = 24
    # Posting-specific fields (only used when action_type == "post_to_page")
    post_image_url: Optional[str] = None         # publicly accessible image URL for Instagram
    post_platforms: Optional[List[str]] = None   # ["facebook"] | ["instagram"] | ["facebook","instagram"]
    post_client_id: Optional[str] = None         # client slug — to look up page credentials


class TaskState(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    operator_number: str = ""            # renamed from rafi_number — any operator, not just Rafi
    workflow: Optional[str] = None
    client_id: Optional[str] = None
    client_profile: Optional[ClientProfile] = None
    pending_caption: Optional[Dict[str, Any]] = None
    media_batch: List[Dict[str, str]] = Field(default_factory=list) # List of {"type": "image"|"video", "path": "..."}
    pending_action: Optional[PendingAction] = None
    lead_phone: Optional[str] = None
    spoke_result: Optional[Dict[str, Any]] = None
    status: str = "active"
    pending_reply: Optional[str] = None
    error_message: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    # ── NEW: these two fields give Jarvis memory ──
    conversation_history: List[Dict[str, str]] = Field(default_factory=list)
    # Stores all turns as [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
    # Trimmed to MAX_HISTORY_TURNS in orchestrator to control token usage.

    known_clients: List[str] = Field(default_factory=list)
    # Cached list of client names — populated on first client lookup, used for "not found" hints.


# ── Session Store ─────────────────────────────────────────────────────────────
# In-memory dict-backed store. Swap for Redis in production when handling
# concurrent users or if the server restarts need to preserve sessions.

class SessionStore:
    """Simple in-memory session store. Replace with Redis for production."""

    def __init__(self):
        self._sessions: Dict[str, TaskState] = {}
        self._phone_to_session: Dict[str, str] = {}

    def get_or_create_session(self, operator_number: str) -> str:
        """Return existing session_id for this number or create a new one."""
        if operator_number in self._phone_to_session:
            session_id = self._phone_to_session[operator_number]
            # Reuse session only if still active
            state = self._sessions.get(session_id)
            if state and state.status in ("active", "awaiting_confirmation"):
                return session_id

        # Create a fresh session
        state = TaskState(operator_number=operator_number)
        self._sessions[state.session_id] = state
        self._phone_to_session[operator_number] = state.session_id
        logger.info(f"[{state.session_id}] New session created for {operator_number}")
        return state.session_id

    def load(self, session_id: str) -> Optional[TaskState]:
        """Load a session by ID. Returns None if not found."""
        return self._sessions.get(session_id)

    def save(self, state: TaskState) -> None:
        """Persist updated state."""
        self._sessions[state.session_id] = state
        logger.debug(f"[{state.session_id}] Session saved. Status: {state.status}")

    def clear(self, session_id: str) -> None:
        """Remove a completed session."""
        state = self._sessions.pop(session_id, None)
        if state:
            self._phone_to_session.pop(state.operator_number, None)
            logger.info(f"[{session_id}] Session cleared.")
