# api/v1.py
"""
Aegis REST API v1 — authenticated scan access for power users and bots.

Authentication: pass API key in the X-API-Key header.
Generate a key with /apikey in the Telegram bot.

Endpoints:
  POST /v1/scan        — fast security scan
  POST /v1/deployer    — deployer forensics
  GET  /v1/me          — key info and usage stats

Rate limit: 500 requests/day per key (resets at midnight UTC).
All responses are JSON.
"""
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["v1"])


# ── Auth helper ───────────────────────────────────────────────────────────────

async def _authenticate(x_api_key: Optional[str]) -> dict:
    """Validate API key, check daily limit. Returns key record."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    from core.db import get_api_key, check_api_key_limit
    key_record = await get_api_key(x_api_key)
    if not key_record:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")
    within_limit = await check_api_key_limit(x_api_key)
    if not within_limit:
        raise HTTPException(status_code=429, detail="Daily request limit reached (500/day). Resets at midnight UTC.")
    # Check that the key owner has an active subscription
    from core.subscription import can_use_service
    allowed, _ = await can_use_service(key_record["user_id"])
    if not allowed:
        raise HTTPException(status_code=403, detail="Subscription expired. Renew at t.me/AegisSecurityBot")
    return key_record


# ── Request / Response models ─────────────────────────────────────────────────

class ScanRequest(BaseModel):
    address: str
    chain: str = "bsc"
    fast: bool = True

class DeployerRequest(BaseModel):
    address: str
    chains: list[str] = ["eth", "bsc", "polygon", "base", "arb"]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/me")
async def api_me(x_api_key: Optional[str] = Header(None)):
    """Return key metadata and today's usage."""
    key_record = await _authenticate(x_api_key)
    return {
        "user_id":        key_record["user_id"],
        "label":          key_record.get("label", ""),
        "requests_today": key_record.get("requests_today", 0),
        "daily_limit":    key_record.get("daily_limit", 500),
        "created_at":     key_record.get("created_at", ""),
    }


@router.post("/scan")
async def api_scan(body: ScanRequest, request: Request,
                   x_api_key: Optional[str] = Header(None)):
    """
    Run a security scan on a smart contract.

    Returns the full risk assessment including score, flags, and summary.
    Set fast=false for a deeper analysis (slower, uses Slither + GoPlus).
    """
    key_record = await _authenticate(x_api_key)
    config = request.app.state.config

    try:
        from core.session import run_scan
        contract, result = await run_scan(
            body.address, body.chain, config, fast_mode=body.fast
        )
    except Exception as e:
        logger.error(f"API scan error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    # Strip internal keys before returning
    clean_result = {k: v for k, v in result.items() if not k.startswith("_")}
    return {
        "address":    body.address,
        "chain":      body.chain,
        "token_name": contract.get("token_name", ""),
        "token_symbol": contract.get("token_symbol", ""),
        "result":     clean_result,
    }


@router.post("/deployer")
async def api_deployer(body: DeployerRequest, request: Request,
                       x_api_key: Optional[str] = Header(None)):
    """
    Run deployer forensics on a wallet address.

    Scans across specified chains, classifies previous deployments
    (rugged/abandoned/active), and returns a reputation score.
    """
    key_record = await _authenticate(x_api_key)
    config = request.app.state.config

    try:
        from core.deployer_session import run_deployer_analysis
        profile, result = await run_deployer_analysis(
            body.address, config, chains=body.chains, stream=False
        )
    except Exception as e:
        logger.error(f"API deployer error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    risk_p = profile.get("risk_profile", {})
    return {
        "address":          body.address,
        "total_deployments": profile.get("total_deployments", 0),
        "rugged_count":     risk_p.get("rugged_count", 0),
        "abandoned_count":  risk_p.get("abandoned_count", 0),
        "active_count":     risk_p.get("active_count", 0),
        "rug_rate":         risk_p.get("rug_rate", 0.0),
        "avg_time_to_rug_hours": risk_p.get("avg_time_to_rug_hours"),
        "reputation_score": result.get("reputation_score", 100),
        "verdict":          result.get("verdict", ""),
        "recommendation":   result.get("recommendation", ""),
        "summary":          result.get("summary", ""),
        "red_flags":        result.get("red_flags", []),
    }