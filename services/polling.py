# services/polling.py
import asyncio
import httpx
from datetime import datetime, timezone

# We should ideally load this from core.config
from core.config import load_config
config = load_config()
MORALIS_API_KEY = config["moralis"]["api_key"]
HISTORY_BASE = "https://deep-index.moralis.io/api/v2.2"
POLL_INTERVAL_SECONDS = 30

_last_cursor: dict[tuple, str | None] = {}


async def poll_wallet_transactions(address: str, chain: str, callback):
    """
    Polls /wallets/{address}/history every POLL_INTERVAL_SECONDS.
    Only delivers transactions newer than the last run.
    callback is an async callable that receives a list of tx dicts.
    """
    headers = {"X-API-Key": MORALIS_API_KEY, "accept": "application/json"}
    key = (address, chain)

    async with httpx.AsyncClient() as client:
        while True:
            params = {"chain": chain, "order": "DESC", "limit": 25}
            cursor = _last_cursor.get(key)
            if cursor:
                params["cursor"] = cursor

            try:
                resp = await client.get(
                    f"{HISTORY_BASE}/wallets/{address}/history",
                    params=params, headers=headers, timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                txs = data.get("result", [])
                if txs:
                    _last_cursor[key] = data.get("cursor")
                    native_txs = [
                        tx for tx in txs
                        if tx.get("category") == "token transfer" or tx.get("value", "0") != "0"
                    ]
                    if native_txs:
                        await callback(native_txs)
            except Exception:
                pass

            await asyncio.sleep(POLL_INTERVAL_SECONDS)
