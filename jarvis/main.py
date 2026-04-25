import asyncio
from core.llm.router import chat
from dotenv import load_dotenv
load_dotenv()

from core.hub.orchestrator import run_hub
from core.spokes.base import Spoke
from pydantic import BaseModel

# --- Fake spoke 1: fetch client info ---
class ClientInfoSpoke(Spoke):
    name = "get_client_info"
    description = "Returns brand profile and tone for a given client name"
    model_role = "spoke_fast"

    class Input(BaseModel):
        client_name: str = ""

    class Output(BaseModel):
        brand_voice: str = ""
        platform: str = ""
        error: str | None = None

    async def run(self, input: "ClientInfoSpoke.Input") -> "ClientInfoSpoke.Output":
        return ClientInfoSpoke.Output(
            brand_voice=f"{input.client_name}'s brand is bold, modern, and uses Arabic + English.",
            platform="Instagram"
        )


class CaptionSpoke(Spoke):
    name = "generate_caption"
    description = "Generates an Instagram caption given a brand voice and content description"
    model_role = "spoke_smart"

    class Input(BaseModel):
        brand_voice: str = ""
        content_description: str = ""

    class Output(BaseModel):
        caption: str = ""
        hashtags: list[str] = []
        error: str | None = None

    async def run(self, input: "CaptionSpoke.Input") -> "CaptionSpoke.Output":
        response = await chat(
            role="spoke_smart",
            messages=[
                {"role": "system", "content": f"You generate Instagram captions. Brand voice: {input.brand_voice}. Return only the caption on the first line, then hashtags comma-separated on the second line."},
                {"role": "user", "content": input.content_description},
            ]
        )
        text = response.choices[0].message.content.strip()
        lines = text.split("\n")
        caption = lines[0].strip()
        hashtags = []
        if len(lines) > 1:
            hashtags = [h.strip() for h in lines[-1].split(",") if h.strip().startswith("#")]
        return CaptionSpoke.Output(caption=caption, hashtags=hashtags)


SYSTEM_PROMPT = """You are Jarvis, an AI operating system for a marketing agency.
You have two tools: get_client_info and generate_caption.
When asked to create content for a client, always get their info first, then generate the caption.
Only use Arabic and English. Never use any other language.
Be concise. Always use your tools — never answer from memory."""

async def main():
    user_input = "Create an Instagram caption for client Rafi about his new office opening"
    print(f"\nUser: {user_input}\n")
    print("Jarvis is thinking...\n")
    result = await run_hub(
        user_message=user_input,
        spokes=[ClientInfoSpoke(), CaptionSpoke()],
        system_prompt=SYSTEM_PROMPT,
    )
    print(f"Jarvis: {result}")

asyncio.run(main())

# give langfuse time to flush
import time
time.sleep(3)