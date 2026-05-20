# core/deployer_session.py
import json
from typing import AsyncGenerator, List, Optional

from core.chains import CHAINS
from fetchers.deployer import DeployerFetcher
from fetchers.nansen import NansenClient, fetch_nansen_deployer_data
from ai.deployer_prompt import build_deployer_prompt
from ai.client import OpenRouterClient
from utils.validators import is_valid_address
from utils.cache import get_cached, set_cached

DEPLOYER_CACHE_KEY_PREFIX = "deployer_v2_nansen_"


def _calculate_fallback_reputation(risk_profile: dict) -> dict:
    """Calculate a basic reputation score from on-chain risk signals when AI fails."""
    rep = 100
    red_flags = []
    findings = []
    
    # Each risk flag reduces score
    unverified_ratio = float(risk_profile.get("unverified_ratio") or 0.0)
    low_holder_ratio = float(risk_profile.get("low_holder_ratio") or 0.0)
    rapid_burst = risk_profile.get("rapid_burst", False)
    
    if unverified_ratio > 0.5:
        red_flags.append("high unverified ratio")
        rep -= 30
    elif unverified_ratio > 0.2:
        red_flags.append("some unverified contracts")
        rep -= 15
    
    if low_holder_ratio > 0.5:
        red_flags.append("many low-holder contracts")
        rep -= 25
    elif low_holder_ratio > 0.2:
        red_flags.append("some low-holder contracts")
        rep -= 10
    
    if rapid_burst:
        red_flags.append("rapid deployment burst detected")
        rep -= 20
    
    # Determine verdict based on score
    if rep >= 80:
        verdict = "RUG HISTORY: UNKNOWN"
        rec = "PROCEED WITH CAUTION - FRESH WALLET"
    elif rep >= 50:
        verdict = "SUSPICIOUS PATTERN"
        rec = "MONITOR LP & TOP HOLDERS"
    else:
        verdict = "HIGH RUG RISK"
        rec = "AVOID - MULTIPLE RISK SIGNALS"
    
    summary = f"On-chain risk analysis identified {len(red_flags)} red flag(s): {', '.join(red_flags) if red_flags else 'none'}."
    
    return {
        "reputation_score": max(0, rep),
        "verdict": verdict,
        "recommendation": rec,
        "summary": summary,
        "red_flags": red_flags,
        "findings": findings,
    }

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

    from utils.validators import is_solana_address
    is_sol = is_solana_address(deployer)

    if is_sol:
        chains_to_scan = ["solana"]
        cache_key = f"{DEPLOYER_CACHE_KEY_PREFIX}{deployer.lower()}_solana"
    else:
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

    if is_sol:
        from aegis_solana.rpc_client import SolanaRPCClient
        from fetchers.solana_deployer import SolanaDeployerFetcher

        rpc_endpoint = config.get("rpc", {}).get("solana", "https://eu.fluxrpc.com")
        flux_api_key = config.get("solana", {}).get("fluxrpc_api_key", "")
        rpc_client = SolanaRPCClient(endpoint=rpc_endpoint, api_key=flux_api_key, debug=debug)
        fetcher = SolanaDeployerFetcher(rpc_client, debug=debug)

        if debug:
            print("[DEBUG] Fetching Solana deployment history...")
        deployments = await fetcher.get_deployment_history(deployer)
        if debug:
            print(f"[DEBUG] Found {len(deployments)} total Solana deployments")

        if debug:
            print("[DEBUG] Analyzing funder...")
        funder_info = await fetcher.analyze_funder(deployer, deployments)
        if debug:
            print(f"[DEBUG] Funder: {funder_info.get('funder_address')}")

        if debug:
            print("[DEBUG] Calculating risk profile...")
        risk_profile = fetcher.calculate_risk_profile(deployments, funder_info)
        chains_active = ["solana"] if deployments else []
    else:
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
        chains_active = list(set(d["chain"] for d in deployments))

    nansen_keys = config.get("nansen", {}).get("api_keys", [])
    nansen_data = {}
    if nansen_keys and not is_sol:
        if debug:
            print(f"[DEBUG] Fetching Nansen reputation and label for {deployer}...")
        try:
            nansen_client = NansenClient(nansen_keys)
            nansen_data = await fetch_nansen_deployer_data(deployer, "eth", nansen_client, debug=debug)
        except Exception as e:
            if debug:
                print(f"[DEBUG] Nansen fetch failed: {e}")

    profile = {
        "deployer": deployer.lower(),
        "total_deployments": len(deployments),
        "chains_active": chains_active,
        "chains_scanned": chains_to_scan,
        "funder": funder_info,
        "deployments": deployments,
        "risk_profile": risk_profile,
        "nansen": nansen_data,
    }

    if len(deployments) == 0:
        result = {
            "reputation_score": 100,
            "verdict": "CLEAN EOA / USER WALLET",
            "recommendation": "SAFE – NOT A DEPLOYER",
            "summary": "This address is a standard user wallet or transaction account. It has never deployed any smart contracts or tokens on-chain, meaning it carries no deployer-associated rug-pull risks.",
            "red_flags": [],
            "findings": []
        }
        await set_cached(deployer, cache_key, {"profile": profile, "result": result})
        if stream:
            async def _stream_fallback():
                yield json.dumps(result)
            return profile, _stream_fallback()
        else:
            return profile, result

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
        async def _stream_with_fallback():
            """Wraps stream_audit to substitute on-chain fallback on AI failure."""
            fallback_used = False
            result_json = ""
            async for chunk in client.stream_audit(messages):
                result_json += chunk
            try:
                final_result = json.loads(result_json)
                if final_result.get("is_fallback"):
                    fallback_used = True
                    final_result = _calculate_fallback_reputation(risk_profile)
                    if debug:
                        print("[DEBUG] Streaming fallback: using on-chain risk profile")
            except json.JSONDecodeError:
                fallback_used = True
                final_result = _calculate_fallback_reputation(risk_profile)
                if debug:
                    print("[DEBUG] Streaming JSON parse failed: using on-chain risk profile")
            if fallback_used:
                await set_cached(deployer, cache_key, {"profile": profile, "result": final_result})
                yield json.dumps(final_result)
            else:
                yield result_json
        return profile, _stream_with_fallback()
    else:
        result = await client.complete(messages)
        if debug:
            print(f"[DEBUG] AI response received, is_fallback={result.get('is_fallback', False)}")
        # If AI fallback triggered, use on-chain risk profile instead
        if isinstance(result, dict) and result.get("is_fallback"):
            if debug:
                print("[DEBUG] AI fallback triggered, using on-chain risk profile")
            result = _calculate_fallback_reputation(risk_profile)
            await set_cached(deployer, cache_key, {"profile": profile, "result": result})
        # Cache valid results (not fallbacks)
        elif isinstance(result, dict) and not result.get("is_fallback"):
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