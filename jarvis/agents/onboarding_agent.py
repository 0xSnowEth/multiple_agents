import json
import logging
from pydantic import BaseModel, Field
from core.spokes.base import Spoke
from core.llm.router import chat
from tools.client_store import write_client_profile
from core.state import ClientProfile

logger = logging.getLogger(__name__)

class OnboardingSpoke(Spoke):
    name = "onboarding_spoke"
    description = "Use this when the client is NOT in the database. Summarize ALL known facts about the client from the conversation history and pass it into 'known_facts'."
    model_role = "spoke_smart"

    class Input(BaseModel):
        client_name: str = Field(..., description="Name of the client")
        known_facts: str = Field(..., description="A dense summary of EVERY fact mentioned about this client in the chat history (e.g. 'Target audience is luxury car buyers. Brand voice is futuristic. Platforms: Twitter, Instagram. English only. Number: +1 800-555-1234')")

    class Output(BaseModel):
        is_complete: bool = Field(False, description="True only if all required fields are fully populated.")
        missing_fields: list[str] = Field(default_factory=list, description="List of missing fields if not complete. Can be empty.")
        next_question_for_operator: str | None = Field(None, description="The exact question to ask the operator to get the missing info.")
        formatted_profile: ClientProfile | None = Field(None, description="The complete ClientProfile object if is_complete is True. YOU MUST PROVIDE A VALID SLUG FOR THE 'id' FIELD (e.g. 'tesla').")

    async def run(self, input: Input) -> Output:
        system_prompt = """You are the Client Onboarding Specialist.
Your job is to evaluate the provided known facts about the client and ensure EVERY required field is present.

Required fields:
- name
- brand_voice
- target_audience
- platforms
- language_preference
- whatsapp_number

If ANY field is missing or "None", set is_complete=False and generate a short, direct question to ask the operator (e.g., "What is the target audience and language preference?").
If ALL fields are present, set is_complete=True and populate formatted_profile with a complete ClientProfile object.

CRITICAL: The 'id' field in ClientProfile is a URL slug. DO NOT ask the operator for an ID. You MUST automatically generate it yourself by converting the Client Name to lowercase and replacing spaces with hyphens.

Generate ONLY valid JSON."""

        user_prompt = f"""Client Name: {input.client_name}
Known Facts:
{input.known_facts}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        try:
            response = await chat(role=self.model_role, messages=messages)
            msg = response.choices[0].message.content
            
            if "```json" in msg:
                msg = msg.split("```json")[1].split("```")[0].strip()
            elif "```" in msg:
                msg = msg.split("```")[1].split("```")[0].strip()
                
            data = json.loads(msg)
            output = self.Output(**data)
            
            # If complete, actually save it to the database!
            if output.is_complete and output.formatted_profile:
                # Use the tool to persist it
                success = await write_client_profile(output.formatted_profile.model_dump())
                if success:
                    import os
                    host_url = os.getenv("HOST_URL", "https://<your-ngrok>.ngrok-free.app")
                    output.next_question_for_operator = (
                        f"✅ Client profile saved.\n\n"
                        f"🔗 *Action Required:* Click this secure link to authorize and connect their Meta Pages:\n"
                        f"{host_url}/api/auth/meta?client_id={output.formatted_profile.id}"
                    )
                else:
                    output.next_question_for_operator = "⚠️ Error: Tried to save the client but the database write failed. Make sure all fields are correctly formatted."
                    
            return output
            
        except Exception as e:
            logger.error(f"Onboarding spoke failed: {e}", exc_info=True)
            return self.Output(
                is_complete=False, 
                missing_fields=[], 
                next_question_for_operator=f"Error processing onboarding: {e}"
            )
