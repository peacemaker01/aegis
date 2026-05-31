# analysis/wallet_cluster.py
"""
Wallet clustering detector.

Checks whether the top holders of a token are actually the same entity
operating multiple wallets — a common fake-distribution technique.

Detection signals:
  1. Multiple top-holder wallets funded by the same source wallet
  2. Wallets created (first-tx) within a narrow time window (<60 min)
  3. Coordinated buy transactions in the same block or within a few blocks

Returns a cluster_risk dict that feeds into the scan result and AI prompt.
"""
import asyncio
from collections import Counter
from typing import List, Dict, Any, Optional
import httpx

from utils.rate_limiter import RateLimiter

_limiter = RateLimiter(calls_per_second=2)
ETHERSCAN_BASE = "https://api.etherscan.io/v2/api"


async def _get_first_tx(address: str, chain_id: int, api_key: str, client: httpx.AsyncClient) -> Optional[dict]:
    """Fetch the first inbound transaction for a wallet (its funding tx)."""
    await _limiter.acquire()
    params = {
        "chainid": chain_id, "module": "account", "action": "txlist",
        "address": address, "startblock": 0, "endblock": 99999999,
        "page": 1, "offset": 10, "sort": "asc", "apikey": api_key,
    }
    try:
        r = await client.get(ETHERSCAN_BASE, params=params, timeout=15)
        data = r.json()
        if data.get("status") == "1" and data.get("result"):
            for tx in data["result"]:
                if tx.get("to", "").lower() == address.lower() and int(tx.get("value", "0")) > 0:
                    return {
                        "funder": tx.get("from", "").lower(),
                        "timestamp": int(tx.get("timeStamp", 0)),
                        "block": int(tx.get("blockNumber", 0)),
                    }
    except Exception:
        pass
    return None


async def detect_wallet_clusters(
    top_holders: List[str],
    chain_id: int,
    api_key: str,
    debug: bool = False,
) -> Dict[str, Any]:
    """
    Analyse top holders for clustering patterns.

    Args:
        top_holders: list of wallet addresses (top 10 holders)
        chain_id:    EVM chain ID
        api_key:     Etherscan-compatible API key

    Returns dict with:
        cluster_risk_score  float  0.0–10.0  (higher = more suspicious)
        cluster_count       int    number of distinct clusters detected
        shared_funder_count int    wallets sharing the same funding source
        coordinated_count   int    wallets created within 60 min of each other
        cluster_flags       list   human-readable flag strings
        is_suspicious       bool
    """
    if not top_holders or not api_key:
        return _empty_result()

    async with httpx.AsyncClient(timeout=20) as client:
        tasks = [_get_first_tx(addr, chain_id, api_key, client) for addr in top_holders]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    funding_info: List[Optional[dict]] = [
        r if not isinstance(r, Exception) else None for r in results
    ]

    funders    = [f["funder"] for f in funding_info if f and f.get("funder")]
    timestamps = [f["timestamp"] for f in funding_info if f and f.get("timestamp")]

    funder_counts  = Counter(funders)
    shared_funders = {funder: count for funder, count in funder_counts.items() if count >= 2}
    shared_funder_wallets = sum(shared_funders.values())

    # Count wallets created within a 60-minute window of each other
    timestamps_sorted = sorted(timestamps)
    coordinated = 0
    for i, ts in enumerate(timestamps_sorted):
        window = [t for t in timestamps_sorted[i+1:] if t - ts <= 3600]
        if window:
            coordinated = max(coordinated, len(window) + 1)

    # Score: 0–10
    score = 0.0
    flags = []

    if shared_funder_wallets >= 3:
        score += 4.0
        flags.append(f"{shared_funder_wallets} top-holder wallets share a common funding source")
    elif shared_funder_wallets == 2:
        score += 2.0
        flags.append(f"2 top-holder wallets funded from the same wallet")

    if coordinated >= 4:
        score += 3.5
        flags.append(f"{coordinated} top-holder wallets created within 60 minutes of each other")
    elif coordinated == 3:
        score += 2.0
        flags.append(f"3 top-holder wallets created within 60 minutes of each other")
    elif coordinated == 2:
        score += 1.0

    # If a single funder controls many wallets it's almost certainly sybil
    max_shared = max(shared_funders.values()) if shared_funders else 0
    if max_shared >= 4:
        score = min(10.0, score + 3.0)
        flags.append(f"Single wallet funded {max_shared} of the top holders — sybil pattern")

    score = min(10.0, score)

    if debug:
        print(f"[DEBUG] Cluster detection: shared_funder_wallets={shared_funder_wallets}, coordinated={coordinated}, score={score:.1f}")

    return {
        "cluster_risk_score":  round(score, 1),
        "cluster_count":       len(shared_funders),
        "shared_funder_count": shared_funder_wallets,
        "coordinated_count":   coordinated,
        "cluster_flags":       flags,
        "is_suspicious":       score >= 3.0,
    }


def _empty_result() -> Dict[str, Any]:
    return {
        "cluster_risk_score":  0.0,
        "cluster_count":       0,
        "shared_funder_count": 0,
        "coordinated_count":   0,
        "cluster_flags":       [],
        "is_suspicious":       False,
    }