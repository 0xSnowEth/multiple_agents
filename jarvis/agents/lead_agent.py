import json
import logging
from pydantic import BaseModel
from core.spokes.base import Spoke
from core.llm.router import chat

logger = logging.getLogger(__name__)

class LeadSpoke(Spoke):
    name = "lead_spoke"
    description = (
        "Qualifies an inbound lead who has messaged the agency asking about services. "
        "Analyzes the lead's message, classifies them as HOT/WARM/COLD, drafts a qualification response."
    )
    model_role = "spoke_smart"

    class Input(BaseModel):
        lead_phone_number: str
        initial_message: str
        additional_context: str = ""

    class Output(BaseModel):
        lead_summary: str | None = None
        qualification_level: str | None = None
        recommended_response: str | None = None
        recommended_action: str | None = None
        key_signals: list[str] | None = None
        error: str | None = None

    async def run(self, input: Input) -> Output:
        system_prompt = """You qualify inbound leads for a marketing agency.
Analyze the message, classify as HOT/WARM/COLD, draft a response, and recommend an action.
Return JSON with lead_summary, qualification_level, recommended_response, recommended_action, key_signals.
Respond with ONLY valid JSON."""

        user_prompt = f"""Lead Number: {input.lead_phone_number}
Message: {input.initial_message}
Context: {input.additional_context}"""

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
