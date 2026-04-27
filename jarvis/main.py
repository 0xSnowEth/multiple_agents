# main.py
# Entry point. Wires FastAPI webhook app + hub. Run with: python main.py
import asyncio
import logging
import os
from dotenv import load_dotenv

load_dotenv(override=True)

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _validate_env() -> None:
    """Fail loudly at startup if critical environment variables are missing."""
    required = [
        "LLM_PROFILE",
        "META_ACCESS_TOKEN",
        "META_PRIMARY_PHONE_ID",
        "RAFI_WHATSAPP_NUMBER",
        "WEBHOOK_VERIFY_TOKEN",
    ]
    
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        logger.warning(
            f"Missing some environment variables: {', '.join(missing)}\n"
            f"You may need to set them for the webhook to function fully."
        )

    logger.info(f"Model Profile: {os.getenv('LLM_PROFILE', 'dev')}")


async def main():
    _validate_env()
    try:
        from interfaces.whatsapp import start_webhook
        await start_webhook()
    except ImportError:
        logger.error("Could not import start_webhook from interfaces.whatsapp. Ensure your interfaces are set up correctly.")
        
if __name__ == "__main__":
    asyncio.run(main())