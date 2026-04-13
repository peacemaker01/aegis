# core/wallet_session.py
"""
Wallet tracker pipeline:
  1. Fetch all token holdings for a wallet
  2. Audit each contract (batch, non-streaming, capped at 10)
  3. Run portfolio risk AI analysis
  4. Return complete picture
"""
import asyncio
from ai.client import OpenRouterClient
from ai.portfolio_prompt import build_portfolio_prompt
from core.chains import get_chain
from core.session import run_audit
from fetchers.wallet import fetch_wallet_holdings
from utils.validators import is_valid_address


MAX_AUDIT_TOKENS = 10      # Audit up to 10 tokens to stay within rate limits


async def run_wallet_tracker(
    wallet: str,
    chain_name: str,
    config: dict,
) -> dict:
    """
    Full wallet tracker pipeline.
    Returns:
    {
      "wallet_snapshot": {...},        # holdings from Etherscan
      "audited_holdings": [...],       # each holding + its audit result
      "portfolio_analysis": {...},     # AI portfolio risk assessment
      "skipped_count": int,            # tokens not audited (>MAX_AUDIT_TOKENS)
    }
    """
    if not is_valid_address(wallet):
        raise ValueError(f"Invalid wallet address: {wallet}")

    chain    = get_chain(chain_name)
    api_key  = config["explorers"].get("etherscan", "")

    # 1. Fetch holdings
    snapshot = await fetch_wallet_holdings(
        wallet, chain["id"], chain["key"], api_key
    )

    holdings  = snapshot.get("holdings", [])
    to_audit  = holdings[:MAX_AUDIT_TOKENS]
    skipped   = len(holdings) - len(to_audit)

    # 2. Audit each token contract concurrently (non-streaming)
    audit_tasks = [
        run_audit(h["contract_address"], chain["key"], config, stream=False)
        for h in to_audit
    ]
    audit_results = await asyncio.gather(*audit_tasks, return_exceptions=True)

    audited = []
    for holding, result in zip(to_audit, audit_results):
        if isinstance(result, Exception):
            audit_data = {"risk_score": 5.0, "recommendation": "UNKNOWN",
                         "error": str(result)}
        else:
            _, audit_data = result
        audited.append({**holding, "audit": audit_data})

    # 3. Portfolio risk AI analysis
    client = OpenRouterClient(
        api_key     = config["openrouter"]["api_key"],
        model       = config["openrouter"]["model"],
        max_tokens  = 1500,
        temperature = 0.1,
    )
    messages         = build_portfolio_prompt(wallet, chain["key"], audited)
    portfolio_result = await client.complete(messages)

    return {
        "wallet_snapshot":    snapshot,
        "audited_holdings":   audited,
        "portfolio_analysis": portfolio_result,
        "skipped_count":      skipped,
    }
