# services/smartmoney.py
import asyncio, time, logging
from typing import List, Dict, Any
import httpx
from utils.cache_redis import get_cached, set_cached

logger = logging.getLogger(__name__)

BIRDEYE_BASE = "https://public-api.birdeye.so"
BIRDEYE_TRENDING = f"{BIRDEYE_BASE}/defi/token_trending"

CACHE_TTL = 300
ACTIVITY_CONCURRENCY = 10
HELIUS_LIMIT = 20                   # reduced from 200

def _parse_token(item: dict) -> dict:
    return {
        "address":            item.get("address", ""),
        "name":               item.get("name", "Unknown"),
        "symbol":             item.get("symbol", "???"),
        "price_usd":          float(item.get("price", 0) or 0),
        "price_change_24h":   float(item.get("price24hChangePercent", 0) or 0),
        "volume_24h_usd":     float(item.get("v24hUSD", 0) or 0),
        "liquidity_usd":      float(item.get("liquidity", 0) or 0),
        "market_cap_usd":     float(item.get("fdv", 0) or 0),
        "rank":               int(item.get("rank", 99)),
        "recent_txs":         0,
    }

async def _fetch_activity(token_address: str, helius_url: str, client: httpx.AsyncClient) -> int:
    """Count recent non-error transactions, cached in Redis for 2 min."""
    cache_key = f"aegis:helius_activity:{token_address}"
    cached = await get_cached(cache_key)
    if cached is not None:
        return cached

    try:
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "getSignaturesForAddress",
            "params": [token_address, {"limit": HELIUS_LIMIT, "commitment": "finalized"}],
        }
        resp = await client.post(helius_url, json=payload, timeout=8.0)
        resp.raise_for_status()
        sigs = resp.json().get("result", [])
        count = len([s for s in sigs if not s.get("err")])
    except Exception:
        count = 0

    await set_cached(cache_key, count, ttl=120)   # 2 min Redis cache
    return count

async def _gather_activity(tokens: list, helius_url: str) -> list:
    sem = asyncio.Semaphore(ACTIVITY_CONCURRENCY)
    async def guarded(t):
        async with sem:
            async with httpx.AsyncClient(timeout=8.0) as client:
                t["recent_txs"] = await _fetch_activity(t["address"], helius_url, client)
    await asyncio.gather(*[guarded(t) for t in tokens], return_exceptions=True)

async def get_smart_money_tokens(
    config: dict, limit: int = 20, force_refresh: bool = False, debug: bool = False,
) -> List[Dict[str, Any]]:
    cache_key = "aegis:smartmoney"
    if not force_refresh:
        cached = await get_cached(cache_key)
        if cached:
            if debug: logger.debug("SmartMoney: serving from Redis cache")
            return cached

    birdeye_keys = config.get("birdeye", {}).get("api_keys", []) or [config.get("birdeye", {}).get("api_key", "")]
    if not birdeye_keys or not birdeye_keys[0]:
        logger.error("BIRDEYE_API_KEY not set")
        return []

    headers = {"X-API-KEY": birdeye_keys[0], "x-chain": "solana", "accept": "application/json"}
    params = {"sort_by": "rank", "sort_type": "asc", "offset": 0, "limit": limit}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(BIRDEYE_TRENDING, headers=headers, params=params)
            if debug: print(f"[DEBUG] SmartMoney: status={resp.status_code}")
            resp.raise_for_status()
            payload = resp.json()
    except Exception as e:
        logger.error(f"Birdeye error: {e}")
        return []

    raw_items = payload.get("data", {}).get("tokens", [])
    tokens = [_parse_token(i) for i in raw_items]

    filtered = [t for t in tokens if t["liquidity_usd"] >= 10_000 and t["price_change_24h"] != 0]
    if not filtered:
        filtered = sorted(tokens, key=lambda t: t["liquidity_usd"], reverse=True)[:10]

    helius_url = config.get("rpc", {}).get("solana", "")
    if helius_url:
        await _gather_activity(filtered, helius_url)

    filtered.sort(key=lambda t: t["recent_txs"], reverse=True)

    await set_cached(cache_key, filtered, ttl=CACHE_TTL)
    return filtered


async def get_radar_insights(tokens: list, config: dict) -> str:
    """Get AI risk insights for the radar feed."""
    from ai.client import OpenRouterClient
    import html
    
    try:
        client = OpenRouterClient(
            api_key=config["openrouter"]["api_key"],
            model=config["openrouter"]["model"],
            max_tokens=250, temperature=0.3, json_mode=False,
        )
        token_lines = "\n".join(
            f"- {t['symbol']} ({t['name']}): risk {t.get('security_score', 'unknown')}/10, "
            f"{t['recent_txs']} txs, {t.get('liquidity_usd',0):.0f} liq"
            for t in tokens[:5]
        )
        prompt = (
            f"You are a risk analyst for 'Degen Flow' — a feed of new/unvetted Solana tokens.\n"
            f"{token_lines}\n\n"
            f"HARD RULES:\n"
            f"1. If address ends with 'pump': BASE RISK 8/10. Label: 'PUMP.FUN RISK'\n"
            f"2. If liquidity/holder data missing: Risk +1, flag 'NO DATA - ASSUME HIGH RISK'\n"
            f"3. NEVER use: Smart Money, Gem, Alpha, Safe, Low Risk, Moderate\n"
            f"4. Output order: RISK first, then BAG ALERT, then CODE, then MAIN RISK, price last\n"
            f"5. Title: '⚠️ DEGEN FLOW — HIGH RISK ⚠️'\n"
            f"6. Mandatory disclaimer: 'CONTEXT: 99.6% of Pump.fun tokens fail. Scores relative to other new launches.'"
        )
        ai_insight = await client.complete([{"role":"user","content":prompt}])
        return html.escape(ai_insight.strip()) if isinstance(ai_insight, str) else ""
    except Exception:
        return ""
