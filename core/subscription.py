# core/subscription.py
"""
Subscription management: trial periods, payment verification, and access control.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from telegram import Update
from telegram.ext import ContextTypes, ApplicationHandlerStop
import base58

from core.db import (
    get_user, create_user, update_user_subscription,
    is_signature_used, mark_signature_used, log_subscription_event, log_usage
)
from core.payment import PaymentVerifier, TokenSplitter
from core.solana_client import SolanaClient
from core.config import load_config

config = load_config()
solana_client = SolanaClient(config["solana"]["rpc_url"])
payment_verifier = PaymentVerifier(solana_client)

# ─────────────────────────────────────────────────────────────────────────────
# Safely load payer keypair from private key (may be 32‑byte seed or 64‑byte full key)
# ─────────────────────────────────────────────────────────────────────────────
_private_key = config["solana"]["payment_receiver_private_key"]
_payer_keypair = None
if _private_key:
    try:
        secret_bytes = base58.b58decode(_private_key)
        if len(secret_bytes) == 64:
            from solders.keypair import Keypair
            _payer_keypair = Keypair.from_bytes(secret_bytes)
        else:
            from solders.keypair import Keypair
            _payer_keypair = Keypair.from_seed(secret_bytes[:32])
    except Exception as e:
        print(f"⚠️  Warning: Invalid PAYMENT_RECEIVER_PRIVATE_KEY in subscription module. ({e})")

token_splitter = TokenSplitter(solana_client, _payer_keypair) if _payer_keypair else None


async def get_or_create_user(user_id: int, username: str = "", first_name: str = "") -> dict:
    user = await get_user(user_id)
    if not user:
        trial_days = config["subscription"]["trial_days"]
        await create_user(user_id, username, first_name, trial_days)
        user = await get_user(user_id)
    return user


def is_trial_active(user: dict) -> bool:
    if not user.get("trial_ends_at"):
        return False
    trial_ends = datetime.fromisoformat(user["trial_ends_at"])
    return datetime.now(timezone.utc) < trial_ends


def is_subscription_active(user: dict) -> bool:
    if not user.get("subscription_expires_at"):
        return False
    expires = datetime.fromisoformat(user["subscription_expires_at"])
    return datetime.now(timezone.utc) < expires


def can_use_service(user: dict) -> Tuple[bool, str]:
    if is_subscription_active(user):
        return True, "subscribed"
    if is_trial_active(user):
        return True, "trial"
    return False, "expired"


async def subscription_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Middleware to check subscription before premium commands."""
    if not update.effective_user:
        return

    free_commands = ["start", "help", "subscribe", "verify", "status"]
    if update.message and update.message.text:
        command = update.message.text.split()[0].strip("/").split("@")[0]
        if command in free_commands:
            return

    user_id = update.effective_user.id
    user = await get_or_create_user(user_id, update.effective_user.username or "", update.effective_user.first_name or "")
    allowed, reason = can_use_service(user)

    if not allowed:
        await update.message.reply_text(
            "⛔ Your subscription or trial has expired.\n"
            "Use /subscribe to renew."
        )
        raise ApplicationHandlerStop()

    context.user_data["db_user"] = user


async def process_verification(user_id: int, signature: str) -> dict:
    """Verify payment, split tokens, activate subscription."""
    if await is_signature_used(signature):
        return {"success": False, "error": "This transaction has already been claimed."}

    result = await payment_verifier.verify_payment(signature)
    if not result["success"]:
        return result

    await mark_signature_used(signature)

    split_sig = ""
    if token_splitter:
        try:
            from solders.pubkey import Pubkey
            source_ata = Pubkey.from_string(result["source_token_account"])
            split_sig = await token_splitter.split_and_send(
                source_ata,
                result["amount"],
                result["amount_raw"],
                result["decimals"]
            )
        except Exception as e:
            print(f"Split error: {e}")

    expires_at = datetime.now(timezone.utc) + timedelta(days=config["subscription"]["subscription_days"])
    await update_user_subscription(user_id, expires_at, result["sender"])

    await log_subscription_event(
        user_id=user_id,
        tx_signature=signature,
        amount_tokens=result["amount"],
        usd_value=result["usd_value"],
        burn_amount=result["amount"] * 0.6,
        treasury_amount=result["amount"] * 0.4,
        split_tx_signature=split_sig
    )

    return {"success": True, "expires_at": expires_at, "split_tx": split_sig}


async def usage_logger(user_id: int, command: str, address: str = "", chain: str = ""):
    await log_usage(user_id, command, address, chain)