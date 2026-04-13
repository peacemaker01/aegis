# core/monitor_session.py
"""
Portfolio monitor pipeline:
  - Periodically re-audits all watched contracts
  - Compares with last known state
  - Fires alerts when risk score jumps or flags change
"""
import asyncio
import json
from datetime import datetime, timezone

from ai.client import OpenRouterClient
from ai.portfolio_prompt import build_monitor_prompt
from core.session import run_audit
from core.watchlist import list_entries, update_entry
from utils.cache import get_cached


async def check_one(
    entry: dict,
    config: dict,
) -> dict:
    """
    Re-audit one watched contract and detect changes.
    Returns a change-detection result dict.
    """
    address = entry["address"]
    chain   = entry["chain"]
    label   = entry["label"]
    threshold = float(entry.get("alert_threshold", 6.0))

    # Run fresh audit (bypasses cache with TTL=0)
    try:
        _, new_result = await run_audit(address, chain, config, stream=False)
    except Exception as e:
        return {
            "address": address, "chain": chain, "label": label,
            "alert": False, "error": str(e),
        }

    new_score = new_result.get("risk_score", 0.0)
    old_score = entry.get("last_risk_score")
    old_result_stub = {
        "risk_score":      old_score,
        "recommendation":  entry.get("last_verdict"),
    }

    # Update watchlist with current result
    now = datetime.now(tz=timezone.utc).isoformat()
    update_entry(
        address, chain,
        last_checked    = now,
        last_risk_score = new_score,
        last_verdict    = new_result.get("recommendation"),
    )

    # Simple threshold-based alert (no AI needed for every check)
    score_jump  = (old_score is not None) and (new_score - old_score >= 2.0)
    over_thresh = new_score >= threshold

    if not (score_jump or over_thresh):
        return {
            "address": address, "chain": chain, "label": label,
            "alert": False, "new_score": new_score, "old_score": old_score,
            "alert_level": "NONE", "message": "No significant change",
        }

    # AI-powered change analysis for real alerts
    client = OpenRouterClient(
        api_key     = config["openrouter"]["api_key"],
        model       = config["openrouter"]["model"],
        max_tokens  = 600,
        temperature = 0.1,
    )
    messages      = build_monitor_prompt(address, chain, label, old_result_stub, new_result)
    change_result = await client.complete(messages)

    return {
        "address":    address,
        "chain":      chain,
        "label":      label,
        "new_score":  new_score,
        "old_score":  old_score,
        **change_result,
    }


async def run_monitor_cycle(config: dict) -> list[dict]:
    """
    Run one full monitor cycle across all active watchlist entries.
    Returns list of check results (only alerts by default).
    """
    entries = list_entries(active_only=True)
    if not entries:
        return []

    # Stagger checks to stay within Etherscan rate limits
    results = []
    for entry in entries:
        result = await check_one(entry, config)
        results.append(result)
        await asyncio.sleep(0.5)   # small gap between contracts

    return results
