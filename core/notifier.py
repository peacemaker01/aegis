# core/notifier.py
"""
Notification handlers for Telegram and WhatsApp.
"""
import os
import httpx
from typing import Optional


class TelegramNotifier:
    """Send alerts via Telegram bot."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    async def send(self, message: str) -> bool:
        """Send a message to the configured Telegram chat."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                payload = {
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                }
                resp = await client.post(self.api_url, json=payload)
                return resp.status_code == 200
        except Exception:
            return False


class WhatsAppNotifier:
    """
    Send alerts via WhatsApp Cloud API.
    Requires: phone_number_id, access_token, from_number, to_number.
    Alternatively, use Twilio.
    """

    def __init__(self, phone_number_id: str, access_token: str, from_number: str, to_number: str):
        self.phone_number_id = phone_number_id
        self.access_token = access_token
        self.from_number = from_number
        self.to_number = to_number
        self.api_url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"

    async def send(self, message: str) -> bool:
        """Send a message via WhatsApp Cloud API."""
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self.to_number,
            "type": "text",
            "text": {"body": message},
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(self.api_url, json=payload, headers=headers)
                return resp.status_code == 201
        except Exception:
            return False


def get_notifier(config: dict, notifier_type: str):
    """Factory to create a notifier based on config."""
    if notifier_type == "telegram":
        bot_token = config.get("telegram", {}).get("bot_token")
        chat_id = config.get("telegram", {}).get("chat_id")
        if bot_token and chat_id:
            return TelegramNotifier(bot_token, chat_id)
    elif notifier_type == "whatsapp":
        phone_number_id = config.get("whatsapp", {}).get("phone_number_id")
        access_token = config.get("whatsapp", {}).get("access_token")
        from_number = config.get("whatsapp", {}).get("from_number")
        to_number = config.get("whatsapp", {}).get("to_number")
        if all([phone_number_id, access_token, from_number, to_number]):
            return WhatsAppNotifier(phone_number_id, access_token, from_number, to_number)
    return None
