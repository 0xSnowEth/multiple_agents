# scheduler_runner.py
# Background process — checks data/scheduled_reminders.json every 15 minutes
# and sends any messages whose send_at time has passed.
# Run as a separate systemd service alongside main.py.
import asyncio
import logging
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scheduler_runner")

POLL_INTERVAL_SECONDS = 15 * 60  # 15 minutes


async def run_scheduler():
    """Poll for due reminders and send them."""
    from tools.scheduler import get_due_reminders, mark_reminder_sent
    from tools.whatsapp import send_whatsapp_message

    logger.info("Scheduler runner started. Polling every 15 minutes.")

    while True:
        try:
            due = await get_due_reminders()

            if due:
                logger.info(f"Found {len(due)} due reminder(s). Sending now.")
            else:
                logger.debug("No due reminders.")

            for reminder in due:
                to_number = reminder["to_number"]
                message = reminder["message"]
                from_number_id = reminder.get("from_number_id", "rafi_primary")
                send_at = reminder["send_at"]

                success = await send_whatsapp_message(
                    to_number=to_number,
                    message=message,
                    from_number_id=from_number_id,
                )

                if success:
                    await mark_reminder_sent(to_number, send_at)
                    logger.info(f"Reminder sent to {to_number} (scheduled: {send_at})")
                else:
                    logger.error(
                        f"Failed to send reminder to {to_number} "
                        f"(scheduled: {send_at}). Will retry next cycle."
                    )

        except Exception as e:
            logger.error(f"Scheduler error: {e}", exc_info=True)

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(run_scheduler())
