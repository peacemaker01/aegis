# core/wallet_session.py
import asyncio
from typing import Dict, Any, List
import httpx

from fetchers.moralis import MoralisClient
from ai.client import OpenRouterClient
from ai.portfolio_prompt import build_portfolio_prompt
from core.session import run_scan
from utils.cache import get_cached, set_cached
from fetchers.wallet import fetch_wallet_holdings

WALLET_CACHE_TTL = 300
BIRDEYE_PRICE = "https://public-api.birdeye.so/defi/price"


async def _fetch_token_price(
    address: str,
    birdeye_key: str,
    client: httpx.AsyncClient,
) -> float:
    """Fetch USD price of a token from Birdeye."""
    try:
        resp = await client.get(
            BIRDEYE_PRICE,
            params={"address": address},
            headers={"X-API-KEY": birdeye_key, "x-chain": "ethereum", "accept": "application/json"},
            timeout=5.0,
        )
        data = resp.json()
        return float(data.get("data", {}).get("value", 0))
    except Exception:
        return 0.0


async def _fetch_eth_price(birdeye_key: str, client: httpx.AsyncClient) -> float:
    """Fetch current ETH price."""
    # WETH address on Ethereum
    return await _fetch_token_price(
        "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", birdeye_key, client
    )


async def run_wallet_tracker(
    address: str,
    chain: str | None,
    config: Dict[str, Any],
    debug: bool = False,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    moralis_key = config.get("moralis", {}).get("api_key", "")
    etherscan_keys = config["explorers"].get("etherscan", [])
    birdeye_key = config.get("birdeye", {}).get("api_key", "")
    if not birdeye_key and isinstance(config.get("birdeye", {}).get("api_keys"), list):
        birdeye_key = config["birdeye"]["api_keys"][0] if config["birdeye"]["api_keys"] else ""

    cache_key = f"wallet_{address.lower()}"
    if not force_refresh:
        cached = await get_cached(address, cache_key)
        if cached:
            if debug:
                print("[DEBUG] Returning cached wallet data")
            return cached

    tokens = []
    total_value = 0.0
    used_fallback = False

    # Try Moralis first ------------------------------------------------
    if moralis_key:
        try:
            moralis = MoralisClient(api_key=moralis_key, debug=debug)
            tokens = await moralis.get_wallet_tokens(address)
            total_value_data = await moralis.get_wallet_net_worth(address)
            total_value = float(total_value_data.get("total_networth_usd", 0.0))
            if debug:
                print(f"[DEBUG] Moralis success: {len(tokens)} tokens, ${total_value:,.2f}")
        except Exception as e:
            if debug:
                print(f"[DEBUG] Moralis failed, falling back to Etherscan: {e}")

    # Fallback to Etherscan -------------------------------------------
    if not tokens and etherscan_keys:
        used_fallback = True
        try:
            api_key = etherscan_keys[0] if isinstance(etherscan_keys, list) else etherscan_keys
            eth_data = await fetch_wallet_holdings(address, 1, "eth", api_key)

            # Fetch token prices to calculate USD values
            async with httpx.AsyncClient(timeout=10.0) as client:
                eth_price = 0.0
                if birdeye_key:
                    eth_price = await _fetch_eth_price(birdeye_key, client)
                
                total_value = float(eth_data.get("native_balance", 0)) * eth_price

                # Only price top 10 from Etherscan to avoid rate limits
                for h in eth_data.get("holdings", [])[:10]:
                    token_addr = h.get("contract_address")
                    token_balance = float(h.get("balance", 0))
                    price = 0.0
                    if birdeye_key:
                        price = await _fetch_token_price(token_addr, birdeye_key, client)
                    usd_value = token_balance * price
                    total_value += usd_value
                    tokens.append({
                        "token_address": token_addr,
                        "chain": "eth",
                        "name": h.get("token_name", ""),
                        "symbol": h.get("token_symbol", ""),
                        "usd_value": usd_value,
                        "balance": token_balance,
                    })
            if debug:
                print(f"[DEBUG] Fallback Etherscan + Birdeye: {len(tokens)} tokens, total ${total_value:.2f}")
        except Exception as e:
            if debug:
                print(f"[DEBUG] Etherscan fallback also failed: {e}")

    # Limit to top 10 tokens for auditing
    tokens_sorted = sorted(tokens, key=lambda x: float(x.get("usd_value", 0)), reverse=True)
    tokens_to_audit = tokens_sorted[:10]

    # Audit concurrently
    audit_tasks = []
    for token in tokens_to_audit:
        token_address = token.get("token_address")
        token_chain = token.get("chain", "eth").lower()
        if token_address and token_chain:
            audit_tasks.append(run_scan(token_address, token_chain, config, stream=False, fast_mode=True))

    audit_results = await asyncio.gather(*audit_tasks, return_exceptions=True)
    audited = []
    for token, result in zip(tokens_to_audit, audit_results):
        if isinstance(result, Exception):
            audit_data = {"risk_score": 5.0, "recommendation": "UNKNOWN", "error": str(result)}
        else:
            _, audit_data = result
        audited.append({**token, "audit": audit_data})

    # Portfolio AI analysis (only if we have tokens or value)
    audited_with_value = [h for h in audited if float(h.get("usd_value", 0)) > 0 or float(h.get("balance", 0)) > 0]
    portfolio_result = {}
    if audited_with_value:
        client = OpenRouterClient(
            api_key=config["openrouter"]["api_key"],
            model=config["openrouter"]["model"],
            max_tokens=1500, temperature=0.1, json_mode=True,
            api_keys=config["openrouter"].get("api_keys")
        )
        messages = build_portfolio_prompt(address, audited_with_value, total_value)
        portfolio_result = await client.complete(messages)

    result = {
        "wallet_snapshot": {
            "address": address,
            "total_tokens": len(tokens),
            "total_value_usd": total_value,
            "chains": list(set(t.get("chain") for t in tokens if t.get("chain"))),
            "all_tokens": tokens,
        },
        "audited_holdings": audited,
        "portfolio_analysis": portfolio_result,
        "skipped_count": max(0, len(tokens) - len(tokens_to_audit)),
        "_fallback": used_fallback,
    }

    await set_cached(address, cache_key, result)
    return result