import json
import logging
from pydantic import BaseModel
from core.spokes.base import Spoke
from core.llm.router import chat

logger = logging.getLogger(__name__)

class ApprovalSpoke(Spoke):
    name = "approval_spoke"
    description = (
        "Drafts a client approval request message for a content draft awaiting sign-off, plus a 24h follow-up reminder. "
        "Use when Rafi needs to send a client their content for approval or follow up on an unanswered approval."
    )
    model_role = "spoke_fast"

    class Input(BaseModel):
        client_name: str
        client_number: str
        draft_content: str
        platform: str
        language_preference: str

    class Output(BaseModel):
        approval_message: str | None = None
        reminder_message: str | None = None
        language: str | None = None
        client_number: str | None = None
        reminder_delay_hours: int | None = None
        error: str | None = None

    async def run(self, input: Input) -> Output:
        system_prompt = """You draft client approval requests for social media content.
Draft a professional approval message and a follow-up reminder (sent after 24h).
Return a JSON object with approval_message, reminder_message, language, client_number, and reminder_delay_hours.
Respond with ONLY valid JSON."""

        user_prompt = f"""Client Name: {input.client_name}
Client Number: {input.client_number}
Draft Content: {input.draft_content}
Platform: {input.platform}
Language Preference: {input.language_preference}"""

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
