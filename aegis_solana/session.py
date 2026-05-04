import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, Tuple, Optional, Callable, Awaitable
from solders.pubkey import Pubkey

from aegis_solana.rpc_client import SolanaRPCClient
from aegis_solana.rugcheck_client import RugCheckClient
from utils.cache import get_cached, set_cached
from utils.health import record_success, record_failure
from core.deployer_session import fetch_deployer_profile as fetch_evm_deployer_profile


def _get_cache_key(mint: str, source_hash: str) -> str:
    return f"solana_scan_{mint}_{source_hash}"


async def _get_deployer_address(raw_data: Dict[str, Any]) -> Optional[str]:
    # Try to get deployer from RugCheck as fallback if available, or stay None
    return None


async def _fetch_cross_chain_deployer_profile(
    deployer_address: str, config: Dict[str, Any], debug: bool = False
) -> Optional[Dict[str, Any]]:
    if not deployer_address:
        return None
    cache_key = f"cross_chain_deployer_{deployer_address.lower()}"
    cached = await get_cached(cache_key, "deployer", ttl=3600)
    if cached:
        return cached
    api_key = config["explorers"].get("etherscan", [])
    if isinstance(api_key, list):
        api_key = api_key[0] if api_key else ""
    if not api_key:
        return None
    try:
        chains_to_scan = ["eth", "bsc", "polygon", "base", "arb", "avax"]
        profile = await fetch_evm_deployer_profile(
            deployer_address, api_key, chains_to_scan=chains_to_scan, enrich=False
        )
        if profile.get("total_deployments", 0) > 0:
            await set_cached(cache_key, "deployer", profile)
            return profile
    except Exception as e:
        if debug:
            print(f"[DEBUG] Cross-chain deployer lookup failed: {e}")
    return None


def _calculate_contract_age(deploy_time: Optional[str]) -> Optional[int]:
    """Calculate contract age in days."""
    if not deploy_time:
        return None
    try:
        deploy_dt = datetime.fromisoformat(deploy_time.replace("Z", "+00:00"))
        age_seconds = (datetime.now(timezone.utc) - deploy_dt).total_seconds()
        return max(0, int(age_seconds / 86400))
    except Exception:
        return None


# Solsniffer liquidity extraction removed


async def run_solana_scan(
    mint: str,
    config: Dict[str, Any],
    debug: bool = False,
    fast_mode: bool = True,
    progress_callback: Optional[Callable[[str], Awaitable[None]]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not (32 <= len(mint) <= 44):
        raise ValueError(f"Invalid Solana mint address: {mint}")

    if debug:
        print(f"[DEBUG] Starting Solana scan for {mint} (fast_mode={fast_mode})")

    rpc_endpoint = config.get("rpc", {}).get("solana", "https://eu.fluxrpc.com")
    flux_api_key = config.get("solana", {}).get("fluxrpc_api_key", "")
    rpc_client = SolanaRPCClient(endpoint=rpc_endpoint, api_key=flux_api_key, debug=debug)

    rugcheck = RugCheckClient(debug=debug)

    mint_pubkey = Pubkey.from_string(mint)

    if progress_callback:
        await progress_callback("📡 Fetching token metadata from Solana RPC...")

    tasks = [
        rpc_client.get_mint_info(mint_pubkey),
        rpc_client.get_token_largest_holders(mint_pubkey),
        rugcheck.get_summary(mint),
        rpc_client.get_token_metadata(mint_pubkey),   # fetch name/symbol
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    mint_info = results[0] if not isinstance(results[0], Exception) else {}
    holders = results[1] if not isinstance(results[1], Exception) else []
    rugcheck_data = results[2] if not isinstance(results[2], Exception) else {"error": str(results[2])}
    metadata = results[3] if len(results) > 3 and not isinstance(results[3], Exception) else {}

    # Enrich metadata with RugCheck tokenMeta as fallback for name/symbol
    if not metadata.get("name") and not metadata.get("symbol"):
        rc_meta = rugcheck_data.get("tokenMeta") or {}
        rc_name = rc_meta.get("name") or ""
        rc_symbol = rc_meta.get("symbol") or ""
        if rc_name or rc_symbol:
            metadata = {**metadata, "name": rc_name, "symbol": rc_symbol}

    goplus_parsed = {"goplus_available": False}

    # Compute LP lock duration (RugCheck only)
    lp_lock_days = None

    if rugcheck_data and not lp_lock_days:
        unlock_ts = rugcheck_data.get("lpLockedUntil")
        if unlock_ts:
            try:
                unlock_dt = datetime.fromtimestamp(int(unlock_ts), tz=timezone.utc)
                days_left = (unlock_dt - datetime.now(timezone.utc)).days
                lp_lock_days = max(0, days_left)
            except Exception:
                pass

    deploy_time = rugcheck_data.get("deployTime") # Best effort from RugCheck if available
    contract_age_days = _calculate_contract_age(deploy_time)
    liquidity_depth = 0.0 # Will be fetched via DexScreener in scoring

    raw_data = {
        "mint": mint,
        "mint_info": mint_info,
        "holders": holders,
        "rugcheck": rugcheck_data,
        "goplus": goplus_parsed,
        "contract_age_days": contract_age_days,
        "liquidity_depth": liquidity_depth,
        "lp_lock_days": lp_lock_days,
        "deep_scan": not fast_mode,
        "metadata": metadata,
    }

    if progress_callback:
        await progress_callback("🕵️ Checking cross-chain deployer history...")

    deployer_address = await _get_deployer_address(raw_data)
    cross_chain_profile = None
    if deployer_address:
        if debug:
            print(f"[DEBUG] Deployer address: {deployer_address}")
        cross_chain_profile = await _fetch_cross_chain_deployer_profile(deployer_address, config, debug)
        raw_data["deployer_address"] = deployer_address
        raw_data["cross_chain_profile"] = cross_chain_profile

    source_hash = hashlib.sha256(str(raw_data).encode()).hexdigest()[:16]
    cache_key = _get_cache_key(mint, source_hash)
    cached_ai = await get_cached(cache_key, "solana", ttl=86400 * 7)

    # Bypass cache if deep scan OR if cached result is INCONCLUSIVE
    if cached_ai and fast_mode and cached_ai.get("recommendation") != "INCONCLUSIVE":
        if debug:
            print("[DEBUG] Returning cached AI result")
        return raw_data, cached_ai

    # AI call removed – deterministic scoring handles everything
    ai_result = {"risk_score": None, "recommendation": "", "summary": "",
                 "consensus_findings": [], "single_source_findings": [], "flags": {}}

    ai_result["_raw"] = {
        "mint_info": mint_info,
        "holders": holders,
        "rugcheck": rugcheck_data,
        "goplus": goplus_parsed,
        "deployer_address": deployer_address,
        "cross_chain_activity": cross_chain_profile is not None,
        "contract_age_days": contract_age_days,
        "liquidity_depth": liquidity_depth,
    }

    await set_cached(cache_key, "solana", ai_result)
    return raw_data, ai_result