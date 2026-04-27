# configs/model_params.py
# Python loader for model_params.yaml.
# Import constants from here — never open the yaml directly in other files.
import os
import yaml

_yaml_path = os.path.join(os.path.dirname(__file__), "model_params.yaml")

with open(_yaml_path) as f:
    _params = yaml.safe_load(f)

# Hub
HUB_MAX_TURNS: int = _params["hub"]["max_turns"]
HUB_MAX_TOKENS: int = _params["hub"]["max_tokens"]
HUB_ALERT_THRESHOLD_USD: float = _params["hub"]["alert_threshold_usd"]

# Spokes — defaults
SPOKE_MAX_TURNS: int = _params["spokes"]["default_max_turns"]
SPOKE_MAX_TOKENS: int = _params["spokes"]["default_max_tokens"]


def get_spoke_params(spoke_name: str) -> dict:
    """Return resolved params for a spoke, with per-spoke overrides applied."""
    overrides = _params["spokes"].get(spoke_name, {})
    return {
        "max_turns": overrides.get("max_turns", SPOKE_MAX_TURNS),
        "max_tokens": overrides.get("max_tokens", SPOKE_MAX_TOKENS),
        "temperature": overrides.get("temperature", _params["spokes"]["temperature"]),
    }
