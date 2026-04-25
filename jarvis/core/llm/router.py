import yaml
import os
import litellm
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True)

litellm.suppress_debug_info = True

# Langfuse tracing — every LLM call is now tracked automatically
litellm.success_callback = ["langfuse"]
litellm.failure_callback = ["langfuse"]

_profile = os.getenv("LLM_PROFILE", "dev")
_models_path = Path(__file__).parent.parent.parent / "models.yaml"
_models = yaml.safe_load(_models_path.read_text())["profiles"][_profile]

def model_for(role: str) -> str:
    return _models[role]

async def chat(*, role: str, messages: list, tools: list | None = None) -> dict:
    kwargs = dict(
        model=model_for(role),
        messages=messages,
    )
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    response = await litellm.acompletion(**kwargs)
    return response