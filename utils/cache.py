# utils/cache.py
"""
Redis‑backed cache with automatic in‑memory fallback.
"""
import json
import time
from typing import Optional, Dict, Any

# ------------------------------------------------------------
# Try to import Redis; if not available, we'll use a local dict
# ------------------------------------------------------------
try:
    import redis.asyncio as aioredis
    _REDIS_INSTALLED = True
except ImportError:
    _REDIS_INSTALLED = False

from core.config import load_config

config = load_config()
REDIS_URL = config.get("redis", {}).get("url", "")

# In‑memory cache used when Redis is not configured or unavailable
_memory_cache: Dict[str, tuple[float, dict]] = {}
_pool = None
_redis_disabled = False


async def _redis():
    """Return a Redis client if possible, else raise RuntimeError."""
    global _pool, _redis_disabled

    if _redis_disabled or not REDIS_URL or not _REDIS_INSTALLED:
        raise RuntimeError("Redis not available")

    if _pool is None:
        try:
            _pool = aioredis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
                single_connection_client=False,
            )
            # Verify connection
            await _pool.ping()
        except Exception:
            _redis_disabled = True
            _pool = None
            raise RuntimeError("Redis connection failed")
    return _pool


# ── Public API ────────────────────────────────────────────────────────────────

async def get_cached(address: str, chain: Optional[str] = None, ttl: Optional[int] = None) -> Optional[Any]:
    """Fetch a cached value. Uses Redis if available, else in‑memory dict."""
    if chain is None:
        key = address
    else:
        key = f"cache:{chain}:{address.lower()}"

    try:
        r = await _redis()
        pipe = r.pipeline()
        pipe.get(key)
        if ttl and chain is not None:
            pipe.ttl(key)
        results = await pipe.execute()
        value_str = results[0]
        if value_str is None:
            return None
        if ttl and chain is not None:
            remaining = results[1]
            if remaining is not None and remaining < ttl:
                return None
        return json.loads(value_str)
    except Exception:
        # Fallback to in‑memory cache
        entry = _memory_cache.get(key)
        if entry:
            expiry, value = entry
            if time.time() < expiry:
                return value
            else:
                del _memory_cache[key]
        return None


async def set_cached(address: str, chain: Any, data: Optional[Any] = None, ttl: int = 3600) -> None:
    """Store *data* for *ttl* seconds. Uses Redis if available, else in‑memory dict."""
    if data is None:
        key = address
        val = chain
        expiry_ttl = ttl
    else:
        key = f"cache:{chain}:{address.lower()}"
        val = data
        expiry_ttl = ttl

    try:
        r = await _redis()
        await r.setex(key, expiry_ttl, json.dumps(val))
    except Exception:
        # Fallback to in‑memory cache
        _memory_cache[key] = (time.time() + expiry_ttl, val)