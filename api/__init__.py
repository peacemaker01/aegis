# api/__init__.py
from fastapi import FastAPI

app = FastAPI(title="Aegis Webhook Receiver", version="1.0.0")


@app.get("/health")
async def health():
    return {"status": "ok"}

from fastapi import Request, HTTPException
from core.cryptomus import verify_raw_webhook_signature
from core.db import update_cryptomus_order_status
from core.subscription import get_or_create_user
from datetime import datetime, timezone, timedelta
import aiosqlite
import json

@app.post("/webhook/cryptomus")
async def cryptomus_webhook(request: Request):
    try:
        raw_body = await request.body()
        payload_str = raw_body.decode('utf-8')
        data = json.loads(payload_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    sign = data.get("sign")
    if not sign:
        raise HTTPException(status_code=400, detail="Missing signature")
        
    # Remove sign from dict and serialize dict without spaces to compute signature,
    # as per Cryptomus documentation, but since we have a utility, let's use it.
    # Actually, Cryptomus expects MD5(base64(json_encode(data_without_sign)) + key)
    # The JSON must be dict dumped with separators=(",", ":") and sorted_keys=False
    data_without_sign = {k: v for k, v in data.items() if k != "sign"}
    raw_payload_for_sign = json.dumps(data_without_sign, separators=(",", ":"))
    
    if not verify_raw_webhook_signature(raw_payload_for_sign, sign):
        # Cryptomus uses slightly different encoding sometimes. We'll log the fail but return 400.
        import logging
        logging.getLogger(__name__).error("Cryptomus webhook signature mismatch")
        raise HTTPException(status_code=400, detail="Invalid signature")

    order_id = data.get("order_id")
    status = data.get("status")
    
    if status in ("paid", "paid_over"):
        # Process order payment
        await update_cryptomus_order_status(order_id, status)
        
        # Grant subscription
        from core.db import get_cryptomus_order, DB_PATH
        order = await get_cryptomus_order(order_id)
        if order:
            user_id = order["user_id"]
            days = order["days"]
            db_user = await get_or_create_user(user_id)
            
            now = datetime.now(timezone.utc)
            if db_user.get("subscription_expires_at"):
                current_exp = datetime.fromisoformat(db_user["subscription_expires_at"])
                if current_exp < now:
                    current_exp = now
            else:
                current_exp = now
                
            new_exp = current_exp + timedelta(days=days)
            
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE users SET subscription_expires_at = ? WHERE user_id = ?",
                               (new_exp.isoformat(), user_id))
                await db.commit()
                
            # Optionally send a success message to the user via Telegram Bot
            # This requires access to the bot application instance, which we can get or leave to a background poller.
            # For now, updating the DB is sufficient to grant access.
            import logging
            logging.getLogger(__name__).info(f"Subscription granted for {user_id} via Cryptomus ({days} days)")
            
    return {"status": "success"}