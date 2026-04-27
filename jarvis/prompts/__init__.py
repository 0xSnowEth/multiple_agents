# prompts/__init__.py
# Prompt loader — loads system prompts from .md files in this directory.
# Edit prompts without touching Python code.
import os


def load_prompt(name: str) -> str:
    """
    Load a prompt by name from prompts/{name}.md
    Raises FileNotFoundError if the file doesn't exist.
    """
    path = os.path.join(os.path.dirname(__file__), f"{name}.md")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Prompt file not found: {path}\n"
            f"Expected a file named '{name}.md' in the prompts/ directory."
        )
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


# Pre-load prompts at import time so startup fails loudly if any are missing
HUB_SYSTEM_PROMPT = load_prompt("hub")
