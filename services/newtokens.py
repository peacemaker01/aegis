# services/newtokens.py
import asyncio, time, logging
from typing import List, Dict, Any
import httpx

logger = logging.getLogger(__name__)

DEXSCREENER_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1"
DEXSCREENER_PAIRS    = "https://api.dexscreener.com/latest/dex/tokens/{address}"

CACHE_TTL = 30
MAX_TOKENS = 25
TOO_NEW_THRESHOLD = 120

def _parse_profile(item: dict) -> dict | None:
    if item.get("chainId") != "solana":
        return None
    address = item.get("tokenAddress", "").strip()
    if not address:
        return None
    return {
        "address":     address,
        "name":        None,
        "symbol":      None,
        "description": item.get("description") or "",
        "icon":        item.get("icon"),
        "dex_url":     item.get("url", ""),
        "price_usd":      None,
        "liquidity_usd":  None,
        "volume_24h_usd": None,
        "created_at":     None,
        "security_score": None,
        "audit_summary":  None,
        "too_new":        False,
    }

async def _fetch_pairs(address: str, client: httpx.AsyncClient) -> dict:
    url = DEXSCREENER_PAIRS.format(address=address)
    try:
        resp = await client.get(url, timeout=6.0)
        resp.raise_for_status()
        data = resp.json()
        pairs = data.get("pairs") or []
        if not pairs:
            return {}
        best = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
        base = best.get("baseToken", {})
        return {
            "name":           base.get("name"),
            "symbol":         base.get("symbol"),
            "price_usd":      float(best.get("priceUsd") or 0),
            "liquidity_usd":  float((best.get("liquidity") or {}).get("usd") or 0),
            "volume_24h_usd": float((best.get("volume") or {}).get("h24") or 0),
            "created_at":     int(best.get("pairCreatedAt")) // 1000 if best.get("pairCreatedAt") else None,
        }
    except Exception:
        return {}

async def _fetch_pairs_only(tokens: list) -> list:
    """Fetch pairs data for all tokens concurrently (fast, no audit)."""
    sem = asyncio.Semaphore(8)
    async def guarded(t):
        async with sem:
            async with httpx.AsyncClient(timeout=6.0, headers={"accept":"application/json"}) as client:
                pd = await _fetch_pairs(t["address"], client)
                if pd:
                    t.update({"name": pd.get("name") or t["name"],
                              "symbol": pd.get("symbol") or t["symbol"],
                              "liquidity_usd": pd.get("liquidity_usd"),
                              "volume_24h_usd": pd.get("volume_24h_usd"),
                              "created_at": pd.get("created_at")})
                    if pd.get("created_at") and (time.time() - pd["created_at"]) < TOO_NEW_THRESHOLD and not pd.get("volume_24h_usd"):
                        t["too_new"] = True
                else:
                    t["too_new"] = True
    await asyncio.gather(*[guarded(t) for t in tokens], return_exceptions=True)


async def get_new_tokens(
    config: dict, limit: int = MAX_TOKENS, force_refresh: bool = False, debug: bool = False,
) -> List[Dict[str, Any]]:
    from utils.cache_redis import get_cached as redis_get, set_cached as redis_set
    cache_key = "aegis:newtokens"

    if not force_refresh:
        cached = await redis_get(cache_key)
        if cached:
            if debug: logger.debug("NewTokens: serving from Redis cache")
            return cached

    try:
        async with httpx.AsyncClient(timeout=8.0, headers={"accept":"application/json"}) as client:
            resp = await client.get(DEXSCREENER_PROFILES)
            resp.raise_for_status()
            raw = resp.json()
    except Exception as e:
        logger.error(f"DexScreener error: {e}")
        return []

    parsed, seen = [], set()
    for item in raw:
        token = _parse_profile(item)
        if token and token["address"] not in seen:
            seen.add(token["address"])
            parsed.append(token)
        if len(parsed) >= limit:
            break

    if not parsed:
        return []

    # Immediate pairs data (no audits)
    await _fetch_pairs_only(parsed)

    await redis_set(cache_key, parsed, ttl=CACHE_TTL)
    return parsed
