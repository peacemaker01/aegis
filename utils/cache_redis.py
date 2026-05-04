# utils/cache_redis.py
import json
import redis.asyncio as aioredis
from core.config import load_config

config = load_config()
REDIS_URL = config.get("redis", {}).get("url", "redis://localhost:6379/0")

_redis: aioredis.Redis | None = None

async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis

async def get_cached(key: str) -> dict | None:
    r = await get_redis()
    data = await r.get(key)
    if data:
        return json.loads(data)
    return None

async def set_cached(key: str, value: dict, ttl: int = 300) -> None:
    r = await get_redis()
    await r.setex(key, ttl, json.dumps(value))
