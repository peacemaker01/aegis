# core/monitor_session.py
"""
Portfolio monitor pipeline:
  - Periodically re-audits all watched contracts
  - Compares with last known state
  - Fires alerts when risk score jumps or flags change
  - Sends notifications via Telegram/WhatsApp if configured
"""
import asyncio
import json
from datetime import datetime, timezone

from ai.client import OpenRouterClient
from ai.portfolio_prompt import build_monitor_prompt
from core.session import run_audit
from core.watchlist import list_entries, update_entry
from core.notifier import get_notifier
from utils.cache import get_cached


async def check_one(
    entry: dict,
    config: dict,
    debug: bool = False,
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
    messages = build_monitor_prompt(address, chain, label, old_result_stub, new_result)
    change_result = await client.complete(messages)

    return {
        "address":    address,
        "chain":      chain,
        "label":      label,
        "new_score":  new_score,
        "old_score":  old_score,
        **change_result,
    }


def _format_alert_message(result: dict) -> str:
    """Format an alert message for Telegram/WhatsApp (HTML for Telegram, plain for WhatsApp)."""
    level = result.get("alert_level", "WARNING")
    icon = "🚨" if level == "CRITICAL" else "⚠️" if level == "WARNING" else "ℹ️"
    label = result.get("label", result.get("address", "Unknown"))[:20]
    chain = result.get("chain", "").upper()
    msg = f"{icon} *{level}* – {label} [{chain}]\n"
    msg += f"{result.get('message', 'Risk changed')}\n"
    if result.get("old_score") is not None:
        msg += f"Score: {result['old_score']:.1f} → {result['new_score']:.1f}\n"
    if result.get("action"):
        msg += f"Action: *{result['action']}*"
    return msg


async def run_monitor_cycle(config: dict, debug: bool = False) -> list[dict]:
    """
    Run one full monitor cycle across all active watchlist entries.
    Returns list of check results (only alerts by default).
    Sends notifications if configured.
    """
    entries = list_entries(active_only=True)
    if not entries:
        return []

    # Initialize notifiers if enabled
    telegram = None
    whatsapp = None
    if config.get("notifications", {}).get("telegram", {}).get("enabled"):
        telegram = get_notifier(config, "telegram")
    if config.get("notifications", {}).get("whatsapp", {}).get("enabled"):
        whatsapp = get_notifier(config, "whatsapp")
    notifiers = [n for n in (telegram, whatsapp) if n]

    results = []
    for entry in entries:
        result = await check_one(entry, config, debug=debug)
        results.append(result)
        # Send alert if needed
        if result.get("alert") and notifiers:
            msg = _format_alert_message(result)
            for notifier in notifiers:
                await notifier.send(msg)
        await asyncio.sleep(0.5)   # small gap between contracts
    return results
