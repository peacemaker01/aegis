# core/cryptomus.py
import json
import base64
import hashlib
import httpx
from typing import Optional, Dict, Any
from core.config import load_config

config = load_config()

def get_cryptomus_credentials():
    merchant_id = config.get("cryptomus", {}).get("merchant_id", "")
    payment_key = config.get("cryptomus", {}).get("payment_key", "")
    return merchant_id, payment_key

def generate_signature(payload: str, payment_key: str) -> str:
    """Generate signature for Cryptomus requests/webhooks."""
    base64_payload = base64.b64encode(payload.encode('utf-8')).decode('utf-8')
    sign_string = base64_payload + payment_key
    return hashlib.md5(sign_string.encode('utf-8')).hexdigest()

async def create_payment_invoice(order_id: str, amount_usd: float) -> Optional[str]:
    """Creates a payment invoice on Cryptomus and returns the payment URL."""
    merchant_id, payment_key = get_cryptomus_credentials()
    if not merchant_id or not payment_key:
        return None

    url = "https://api.cryptomus.com/v1/payment"
    payload_dict = {
        "amount": str(amount_usd),
        "currency": "USD",
        "order_id": order_id,
        "url_return": "https://t.me/YourAegisBot",
        "url_callback": config.get("webhook", {}).get("base_url", "http://localhost:8000") + "/webhook/cryptomus",
        "is_payment_multiple": False,
        "lifetime": 3600
    }
    
    payload_str = json.dumps(payload_dict)
    signature = generate_signature(payload_str, payment_key)
    
    headers = {
        "merchant": merchant_id,
        "sign": signature,
        "Content-Type": "application/json"
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, headers=headers, json=payload_dict)
            if response.status_code == 200:
                data = response.json()
                if data.get("state") == 0:
                    return data.get("result", {}).get("url")
            return None
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Cryptomus create_payment error: {e}")
        return None

def verify_webhook_signature(data: Dict[str, Any], sign_header: str) -> bool:
    """Verifies the signature of an incoming Cryptomus webhook."""
    _, payment_key = get_cryptomus_credentials()
    if not payment_key:
        return False
        
    # Cryptomus requires stringifying the raw payload minus the sign if it's in the body,
    # but their docs say we just encode the JSON string as it was sent, but order matters.
    # Actually, it's simpler: Cryptomus sends JSON. The MD5 is MD5(base64(json_body) + payment_key)
    # The payload we received must be identically encoded.
    
    # We will let the caller pass the raw body string to be safe.
    pass

def verify_raw_webhook_signature(raw_body: str, sign_header: str) -> bool:
    """Verifies the signature of an incoming Cryptomus webhook using the raw request body."""
    _, payment_key = get_cryptomus_credentials()
    if not payment_key:
        return False
        
    expected_sign = generate_signature(raw_body, payment_key)
    return expected_sign == sign_header
