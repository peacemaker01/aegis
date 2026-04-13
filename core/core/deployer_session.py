# core/deployer_session.py
"""
Orchestrates the full deployer forensics pipeline:
fetch → AI analysis → return result
"""
import json
import asyncio
from typing import AsyncGenerator

from core.chains import CHAINS
from fetchers.deployer import fetch_deployer_profile
from ai.deployer_prompt import build_deployer_prompt
from ai.client import OpenRouterClient
from utils.validators import is_valid_address
from utils.cache import get_cached, set_cached

DEPLOYER_CACHE_KEY_PREFIX = "deployer_"


async def run_deployer_analysis(
    deployer: str,
    config: dict,
    chains: list[str] | None = None,
    stream: bool = True,
) -> tuple[dict, dict | AsyncGenerator]:
    """
    Full deployer forensics pipeline.

    Returns: (profile_dict, ai_result_or_generator)
    """
    if not is_valid_address(deployer):
        raise ValueError(f"Invalid wallet address: {deployer}")

    api_key = config["explorers"].get("etherscan", "")
    if not api_key:
        raise ValueError(
            "Etherscan API key required for deployer forensics.\n"
            "Set it with: aegis config set explorers.etherscan YOUR_KEY"
        )

    chains_to_scan = chains or ["eth", "bsc", "polygon", "base", "arb"]

    # Cache key includes chain list so different scans are cached separately
    cache_key = f"{DEPLOYER_CACHE_KEY_PREFIX}{deployer.lower()}_{'_'.join(sorted(chains_to_scan))}"

    cached = get_cached(deployer, cache_key)
    if cached and cached.get("total_deployments") is not None:
        profile = cached
    else:
        profile = await fetch_deployer_profile(
            deployer, api_key,
            chains_to_scan=chains_to_scan,
            enrich=True,
        )
        set_cached(deployer, cache_key, profile)

    messages = build_deployer_prompt(profile)

    client = OpenRouterClient(
        api_key     = config["openrouter"]["api_key"],
        model       = config["openrouter"]["model"],
        max_tokens  = config["openrouter"]["max_tokens"],
        temperature = 0.1,
    )

    if stream:
        return profile, client.stream_audit(messages)
    else:
        result = await client.complete(messages)
        return profile, result
