# tools/client_store.py
# Read, list, and write client profiles stored as JSON files.
# Storage: data/clients/{client_id}.json
# These are read-only for spokes. Hub writes on client onboarding.
import os
import json
import logging
from pydantic import BaseModel
from core.state import ClientProfile

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "clients")


class ReadClientProfileInput(BaseModel):
    client_id: str


class ReadClientProfileResult(BaseModel):
    success: bool
    profile: dict | None = None
    error: str | None = None


class ListClientsResult(BaseModel):
    success: bool
    clients: list[dict] = []
    error: str | None = None


class WriteClientProfileInput(BaseModel):
    profile: dict


class WriteClientProfileResult(BaseModel):
    success: bool
    error: str | None = None


async def read_client_profile(client_id: str) -> dict:
    """
    Load a ClientProfile from data/clients/{client_id}.json.
    Returns the profile as a dict on success.
    Returns {"error": "Client '{client_id}' not found"} if missing.
    Never raises.
    """
    path = os.path.join(DATA_DIR, f"{client_id}.json")
    try:
        if not os.path.exists(path):
            logger.warning(f"Client profile not found: {client_id}")
            return {"error": f"Client '{client_id}' not found. Use list_clients to see available clients."}

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Validate against Pydantic model to catch corrupt files
        profile = ClientProfile(**data)
        logger.debug(f"Loaded client profile: {client_id}")
        return profile.model_dump()

    except json.JSONDecodeError as e:
        logger.error(f"Corrupt JSON for client {client_id}: {e}")
        return {"error": f"Client file for '{client_id}' is corrupt. Contact support."}
    except Exception as e:
        logger.error(f"Unexpected error reading client {client_id}: {e}")
        return {"error": str(e)}


async def list_clients() -> list[dict]:
    """
    Return a list of {id, name} dicts for all clients in data/clients/.
    Returns empty list if no clients found. Never raises.
    """
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        clients = []
        for filename in os.listdir(DATA_DIR):
            if not filename.endswith(".json"):
                continue
            client_id = filename[:-5]
            path = os.path.join(DATA_DIR, filename)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                clients.append({"id": client_id, "name": data.get("name", client_id)})
            except Exception as e:
                logger.warning(f"Could not read client file {filename}: {e}")
                continue

        logger.debug(f"Listed {len(clients)} clients")
        return clients

    except Exception as e:
        logger.error(f"Error listing clients: {e}")
        return []


async def write_client_profile(profile) -> bool:
    """
    Write or overwrite a client profile to data/clients/{profile['id']}.json.
    Accepts either a dict or a ClientProfile Pydantic model.
    Returns True on success, False on failure. Never raises.
    """
    try:
        # Normalize input: if it's already a Pydantic model, dump it to dict
        if isinstance(profile, ClientProfile):
            data = profile.model_dump()
        elif isinstance(profile, dict):
            data = profile
        else:
            # Try to treat as dict-like
            data = dict(profile)

        # Validate by constructing a ClientProfile
        validated = ClientProfile(**data)
        os.makedirs(DATA_DIR, exist_ok=True)
        path = os.path.join(DATA_DIR, f"{validated.id}.json")

        with open(path, "w", encoding="utf-8") as f:
            json.dump(validated.model_dump(), f, ensure_ascii=False, indent=2)

        logger.info(f"Wrote client profile: {validated.id}")
        return True

    except Exception as e:
        logger.error(f"Failed to write client profile: {e}", exc_info=True)
        return False
