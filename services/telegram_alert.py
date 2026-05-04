# services/telegram_alert.py
import asyncio
from telegram import Bot
from core.config import load_config

config = load_config()
bot = Bot(token=config["telegram"]["bot_token"])


async def send_telegram_alert_async(chat_id: str, message: str) -> None:
    """Async version – main implementation."""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        print(f"Failed to send Telegram alert: {e}")


def send_telegram_alert_sync(chat_id: str, message: str) -> None:
    """Synchronous wrapper for places where async is impossible."""
    try:
        asyncio.run(send_telegram_alert_async(chat_id, message))
    except RuntimeError:
        # Already inside a running loop (should not happen if called from sync context)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(send_telegram_alert_async(chat_id, message))