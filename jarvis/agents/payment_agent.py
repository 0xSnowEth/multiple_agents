import json
import logging
from pydantic import BaseModel
from core.spokes.base import Spoke
from core.llm.router import chat

logger = logging.getLogger(__name__)

class PaymentSpoke(Spoke):
    name = "payment_spoke"
    description = (
        "Drafts a payment reminder message for a client who has not paid an invoice. "
        "Composes a professional, non-confrontational reminder calibrated to urgency."
    )
    model_role = "spoke_fast"

    class Input(BaseModel):
        client_name: str
        invoice_description: str
        amount: str = "Unknown"
        days_overdue: str = "Unknown"
        language: str = "english"

    class Output(BaseModel):
        reminder_message: str | None = None
        language: str | None = None
        tone_level: str | None = None
        recommended_follow_up_days: int | None = None
        error: str | None = None

    async def run(self, input: Input) -> Output:
        system_prompt = """You draft payment reminders for an agency.
Draft a professional reminder.
Return JSON with reminder_message, language, tone_level, recommended_follow_up_days.
Respond with ONLY valid JSON."""

        user_prompt = f"""Client Name: {input.client_name}
Invoice: {input.invoice_description}
Amount: {input.amount}
Days Overdue: {input.days_overdue}
Language: {input.language}"""

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
