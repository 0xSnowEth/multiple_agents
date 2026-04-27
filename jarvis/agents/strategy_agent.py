import json
import logging
from pydantic import BaseModel, Field
from core.spokes.base import Spoke
from core.llm.router import chat

logger = logging.getLogger(__name__)

class StrategySpoke(Spoke):
    name = "strategy_spoke"
    description = (
        "Generates a full social media campaign plan for a client based on a brief from the operator. "
        "Use when Rafi asks for a campaign plan, content strategy, or monthly content roadmap for a specific client."
    )
    model_role = "spoke_smart"

    class Input(BaseModel):
        client_profile: str = "Unknown"
        campaign_goal: str = "general awareness"
        target_audience: str = "general"
        budget_range: str = "not specified"
        timeline: str = "1 month"

    class Output(BaseModel):
        campaign_name: str | None = None
        concept: str | None = None
        content_pillars: list[str] | None = None
        weekly_posts: int | None = None
        content_mix: str | None = None
        budget_breakdown: str | None = None
        kpis: list[str] | None = None
        timeline_weeks: int | None = None
        notes: str | None = None
        error: str | None = None

    async def run(self, input: Input) -> Output:
        system_prompt = """You are a social media strategy specialist.
Generate a comprehensive social media campaign plan based on the client brief.
Return a JSON object with campaign_name, concept, content_pillars, weekly_posts, content_mix, budget_breakdown, kpis, timeline_weeks, and notes.
Respond with ONLY valid JSON — no explanations, no markdown."""

        user_prompt = f"""Client Profile: {input.client_profile}
Campaign Goal: {input.campaign_goal}
Target Audience: {input.target_audience}
Budget Range: {input.budget_range}
Timeline: {input.timeline}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        logger.info(f"Strategy spoke invoked for: {input.client_profile}")

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
            logger.error(f"Strategy spoke failed: {e}", exc_info=True)
            return self.Output(error=str(e))
