# utils/cache.py
"""
Redis-backed cache for Aegis.
Replaces the previous file-based cache.  All modules that import
get_cached / set_cached continue to work without changes.
"""
import json
import time
from typing import Optional

import redis.asyncio as aioredis
from core.config import load_config

config = load_config()
REDIS_URL = config.get("redis", {}).get("url", "redis://localhost:6379/0")

_pool: aioredis.Redis | None = None

async def _redis():
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _pool

# ── Public API ────────────────────────────────────────────────────────────────

async def get_cached(address: str, chain: str, ttl: Optional[int] = None) -> Optional[dict]:
    """
    Fetch a cached value.  The key is {chain}:{address}.
    If *ttl* is provided, the value is only returned if its remaining
    lifetime is >= ttl (approximate).
    """
    r = await _redis()
    key = f"cache:{chain}:{address.lower()}"
    pipe = r.pipeline()
    pipe.get(key)
    if ttl:
        pipe.ttl(key)
    results = await pipe.execute()
    value_str = results[0]
    if value_str is None:
        return None
    remaining = results[1] if ttl else None
    if ttl and remaining is not None and remaining < ttl:
        return None
    return json.loads(value_str)

async def set_cached(address: str, chain: str, data: dict, ttl: int = 3600) -> None:
    """Store *data* for *ttl* seconds."""
    r = await _redis()
    key = f"cache:{chain}:{address.lower()}"
    await r.setex(key, ttl, json.dumps(data))
