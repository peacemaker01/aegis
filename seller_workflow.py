#!/usr/bin/env python3
"""
Aegis — Automated Seller Webhook Handler
==========================================
Deploy this on a cheap VPS or Vercel serverless function.
When a buyer purchases on Whop/Gumroad, it:
  1. Receives the webhook with buyer device_id
  2. Signs the device_id with your private key
  3. Returns the license key (Whop emails it automatically)

For Whop: set your webhook URL in the Whop seller dashboard.
For Gumroad: use Gumroad's ping webhook → Zapier → this endpoint.

Environment variables required:
  DEXAI_PRIVATE_KEY_PATH  → path to keys/private.pem
  DEXAI_WEBHOOK_SECRET    → your webhook signing secret

Run locally for testing:
  DEXAI_PRIVATE_KEY_PATH=keys/private.pem python seller_workflow.py
"""
import os
import sys
import hmac
import hashlib
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from core.license import sign_license, get_device_id


# ── Config ────────────────────────────────────────────────────────────────────
PRIVATE_KEY_PATH = os.environ.get("DEXAI_PRIVATE_KEY_PATH", "keys/private.pem")
WEBHOOK_SECRET   = os.environ.get("DEXAI_WEBHOOK_SECRET", "changeme")


def load_private_key() -> str:
    path = Path(PRIVATE_KEY_PATH)
    if not path.exists():
        raise FileNotFoundError(f"Private key not found: {path}")
    return path.read_text()


def generate_license_for_device(device_id: str) -> str:
    """Core function — takes a device_id, returns a license key."""
    priv_pem = load_private_key()
    return sign_license(device_id, priv_pem)


# ── Simple HTTP handler (use with Flask or raw http.server) ───────────────────
def handle_whop_webhook(payload: dict) -> dict:
    """
    Whop sends a webhook when a purchase is completed.
    Buyer must include their device_id in the checkout notes
    OR you collect it via a post-purchase form.

    Expected payload:
    {
      "event": "purchase.completed",
      "data": {
        "buyer_email": "...",
        "product_id":  "...",
        "metadata": {
          "device_id": "abc123def456789a"
        }
      }
    }
    """
    event = payload.get("event", "")
    if event != "purchase.completed":
        return {"status": "ignored", "event": event}

    device_id = (
        payload.get("data", {})
               .get("metadata", {})
               .get("device_id", "")
    )
    if not device_id or len(device_id) != 16:
        return {
            "status": "error",
            "message": "device_id missing or invalid. "
                       "Buyer must run: aegis device-id"
        }

    license_key = generate_license_for_device(device_id)
    return {
        "status":      "ok",
        "device_id":   device_id,
        "license_key": license_key,
        "message":     f"Run: aegis activate {license_key}",
    }


# ── CLI usage ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) == 2:
        device_id   = sys.argv[1]
        license_key = generate_license_for_device(device_id)
        print(f"\nDevice ID:   {device_id}")
        print(f"License Key: {license_key}")
        print(f"\nSend buyer:  aegis activate {license_key}\n")
    else:
        # Self-test with this machine's device ID
        my_id  = get_device_id()
        my_key = generate_license_for_device(my_id)
        print(f"\n[SELF TEST]")
        print(f"Device ID:   {my_id}")
        print(f"License Key: {my_key}")
        print(f"\nTest activation:")
        print(f"  DEXAI_DEV=0 python main.py activate {my_key}")
        print()
