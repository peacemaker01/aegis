# core/deployer_session.py
import json
import asyncio
from typing import AsyncGenerator, List, Optional

from core.chains import CHAINS
from fetchers.deployer import DeployerFetcher
from ai.deployer_prompt import build_deployer_prompt
from ai.client import OpenRouterClient
from utils.validators import is_valid_address
from utils.cache import get_cached, set_cached

DEPLOYER_CACHE_KEY_PREFIX = "deployer_v2_"

async def run_deployer_analysis(
    deployer: str,
    config: dict,
    chains: Optional[List[str]] = None,
    stream: bool = True,
    debug: bool = False,
    force_refresh: bool = False,
) -> tuple[dict, dict | AsyncGenerator]:
    if debug:
        print(f"[DEBUG] Starting deployer analysis for {deployer}")

    if not is_valid_address(deployer):
        raise ValueError(f"Invalid wallet address: {deployer}")

    api_keys = config["explorers"].get("etherscan", [])
    if isinstance(api_keys, str):
        api_keys = [api_keys] if api_keys else []
    if not api_keys:
        raise ValueError("Etherscan API key required for deployer forensics.")

    chains_to_scan = chains or ["eth", "bsc", "polygon", "base", "arb"]
    cache_key = f"{DEPLOYER_CACHE_KEY_PREFIX}{deployer.lower()}_{'_'.join(sorted(chains_to_scan))}"

    if not force_refresh:
        cached = await get_cached(deployer, cache_key)
        if cached and cached.get("profile"):
            if debug:
                print("[DEBUG] Returning cached result (use force_refresh=True to bypass)")
            profile = cached["profile"]
            result = cached.get("result", {})
            if not stream:
                return profile, result

    fetcher = DeployerFetcher(api_keys, debug=debug)

    if debug:
        print(f"[DEBUG] Fetching deployment history across {chains_to_scan}")
    deployments = await fetcher.get_deployment_history(deployer, chains_to_scan)
    if debug:
        print(f"[DEBUG] Found {len(deployments)} total deployments")

    if debug:
        print("[DEBUG] Analyzing funder...")
    funder_info = await fetcher.analyze_funder(deployer, deployments, api_keys[0])
    if debug:
        print(f"[DEBUG] Funder: {funder_info.get('funder_address')}")

    if debug:
        print("[DEBUG] Calculating risk profile...")
    risk_profile = fetcher.calculate_risk_profile(deployments, funder_info)
    if debug:
        print(f"[DEBUG] Reputation score: {risk_profile.get('reputation_score')}/100")
        print(f"[DEBUG] Risk flags: {risk_profile.get('risk_flags')}")

    chains_active = list(set(d["chain"] for d in deployments))

    profile = {
        "deployer": deployer.lower(),
        "total_deployments": len(deployments),
        "chains_active": chains_active,
        "chains_scanned": chains_to_scan,
        "funder": funder_info,
        "deployments": deployments,
        "risk_profile": risk_profile,
    }

    if debug:
        print("[DEBUG] Building AI prompt and calling OpenRouter...")

    messages = build_deployer_prompt(profile)
    client = OpenRouterClient(
        api_key=config["openrouter"]["api_key"],
        model=config["openrouter"]["model"],
        max_tokens=1500,
        temperature=0.1,
        json_mode=True,
        api_keys=config["openrouter"].get("api_keys"),
    )

    if stream:
        return profile, client.stream_audit(messages)
    else:
        result = await client.complete(messages)
        if debug:
            print("[DEBUG] AI response received")
        await set_cached(deployer, cache_key, {"profile": profile, "result": result})
        return profile, result

# ---------- Backward-compatible wrapper for Solana module ----------
async def fetch_deployer_profile(
    deployer: str,
    api_key: str | list,
    chains_to_scan: list[str] | None = None,
    enrich: bool = False,
) -> dict:
    """
    Compatibility wrapper that mimics the old `fetch_deployer_profile`.
    Used by the Solana cross-chain forensics lookup.
    """
    if isinstance(api_key, list):
        keys = api_key
    else:
        keys = [api_key]
    
    chains = chains_to_scan or ["eth", "bsc", "polygon", "base", "arb"]
    fetcher = DeployerFetcher(keys)
    deployments = await fetcher.get_deployment_history(deployer, chains)
    
    chains_active = list(set(d["chain"] for d in deployments))
    unverified = [d for d in deployments if not d.get("verified", True)]
    low_holders = [d for d in deployments if d.get("holder_count", 0) < 50]
    
    rapid = False
    timestamps = sorted([d["timestamp"] for d in deployments])
    for i in range(len(timestamps) - 2):
        if timestamps[i + 2] - timestamps[i] < 30 * 86400:
            rapid = True
            break

    return {
        "deployer": deployer.lower(),
        "total_deployments": len(deployments),
        "chains_active": chains_active,
        "chains_scanned": chains,
        "funder": {"funder_address": "unknown", "funding_tx": "", "funding_date": "", "funding_value_eth": ""},
        "deployments": deployments,
        "risk_signals": {
            "multi_chain_deployer": len(chains_active) > 1,
            "has_unverified_contracts": len(unverified) > 0,
            "unverified_count": len(unverified),
            "rapid_deployments": rapid,
            "low_holder_contracts": len(low_holders),
            "total_contracts": len(deployments),
        },
    }