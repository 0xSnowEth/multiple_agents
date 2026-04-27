import json
import logging
from pydantic import BaseModel, Field
from core.spokes.base import Spoke
from core.llm.router import chat

logger = logging.getLogger(__name__)

class CaptionSpoke(Spoke):
    name = "caption_spoke"
    description = "Generates bilingual social media captions (Kuwaiti Arabic + English) for a specific client and post context."
    model_role = "spoke_smart"

    class Input(BaseModel):
        client_name: str = "Unknown"
        brand_voice: str = "professional"
        target_audience: str = "general"
        language_preference: str = "both"
        platform: str = "instagram"
        post_direction: str = "general brand content"
        brand_examples: list[str] = Field(
            default_factory=list, 
            description="A list of string examples for brand voice context. Must be an array."
        )
        notes: str = "none"

    class Output(BaseModel):
        arabic_caption: str | None = None
        english_caption: str | None = None
        arabic_hashtags: list[str] | None = None
        english_hashtags: list[str] | None = None
        hook_strength: str | None = None
        notes: str | None = None
        error: str | None = None

    async def run(self, input: Input) -> Output:
        lang_pref = input.language_preference.lower()
        if "english" in lang_pref and "arabic" not in lang_pref:
            lang_instruction = "ONLY English"
            schema_fields = '"english_caption": "string", "english_hashtags": ["tag1", "tag2"]'
        elif "arabic" in lang_pref and "english" not in lang_pref:
            lang_instruction = "ONLY Kuwaiti Gulf Arabic"
            schema_fields = '"arabic_caption": "string", "arabic_hashtags": ["tag1", "tag2"]'
        else:
            lang_instruction = "BOTH Kuwaiti Gulf Arabic AND English"
            schema_fields = '"arabic_caption": "string", "english_caption": "string", "arabic_hashtags": ["tag1", ...], "english_hashtags": ["tag1", ...]'

        system_prompt = f"""You are a social media caption specialist for a premium marketing agency in the Gulf.

Your job: Generate compelling, on-brand social media captions in {lang_instruction}. Do NOT generate captions for languages that were not requested.

Requirements:
1. Respect the client's brand voice and target audience exactly
2. Generate captions that drive engagement (likes, comments, shares)
3. Include relevant, strategic hashtags
4. Rate the hook strength (probability of engagement): 'strong' | 'medium' | 'weak'
5. Respond with ONLY valid JSON — no explanations, no markdown

Output JSON schema:
{{
  {schema_fields},
  "hook_strength": "strong|medium|weak",
  "notes": "why this caption, what makes it work, any alternatives considered"
}}"""

        examples_list = input.brand_examples or []

        user_prompt = f"""Client: {input.client_name}
Brand voice: {input.brand_voice}
Target audience: {input.target_audience}
Platform: {input.platform}
Post direction / theme: {input.post_direction}
Language preference: {input.language_preference}
Brand examples:
{chr(10).join(f'  - {ex}' for ex in examples_list) or '  (none provided)'}
Additional notes: {input.notes}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        logger.info(f"Caption spoke invoked for: {input.client_name}")

        try:
            response = await chat(role=self.model_role, messages=messages)
            msg = response.choices[0].message
            content = msg.content
            
            # Extract JSON if wrapped in markdown blocks
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            data = json.loads(content)
            return self.Output(**data)
        except Exception as e:
            logger.error(f"Caption spoke failed: {e}", exc_info=True)
            return self.Output(error=str(e))
