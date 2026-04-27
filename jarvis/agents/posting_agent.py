import json
import logging
from pydantic import BaseModel
from core.spokes.base import Spoke
from core.llm.router import chat

logger = logging.getLogger(__name__)

class PostingSpoke(Spoke):
    name = "posting_spoke"
    description = (
        "Prepares a publishing plan for a finalized caption across Facebook and/or Instagram. "
        "Decides which variant goes on which platform, checks for blockers."
    )
    model_role = "spoke_fast"

    class Input(BaseModel):
        client_profile: str
        arabic_caption: str
        english_caption: str
        target_platforms: list[str]
        image_url: str = ""

    class Output(BaseModel):
        posts: list[dict] | None = None
        summary: str | None = None
        error: str | None = None

    async def run(self, input: Input) -> Output:
        system_prompt = """You prepare social media posting plans.
Return JSON with an array of 'posts' (platform, caption, image_url, ready, blocker) and a 'summary'.
Respond with ONLY valid JSON."""

        user_prompt = f"""Client Profile: {input.client_profile}
Arabic Caption: {input.arabic_caption}
English Caption: {input.english_caption}
Target Platforms: {input.target_platforms}
Image URL: {input.image_url}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        try:
            response = await chat(role=self.model_role, messages=messages)
            msg = response.choices[0].message
            content = msg.content
            
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            data = json.loads(content)
            return self.Output(**data)
        except Exception as e:
            return self.Output(error=str(e))
