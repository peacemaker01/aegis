# fetchers/wallet.py
"""
Wallet token holding fetcher.
Uses Etherscan V2 tokentx endpoint to discover current ERC-20 holdings.
"""
import asyncio
from collections import defaultdict
import httpx

from utils.rate_limiter import RateLimiter

wallet_limiter = RateLimiter(calls_per_second=3)
BASE = "https://api.etherscan.io/v2/api"


async def _get(params: dict, api_key: str, client: httpx.AsyncClient) -> dict:
    await wallet_limiter.acquire()
    params["apikey"] = api_key
    try:
        r = await client.get(BASE, params=params, timeout=25)
        return r.json()
    except Exception:
        return {"status": "0", "result": []}


async def get_native_balance(
    wallet: str, chain_id: int, api_key: str, client: httpx.AsyncClient
) -> str:
    """Return native balance in wei as string."""
    data = await _get(
        {"chainid": chain_id, "module": "account",
         "action": "balance", "address": wallet, "tag": "latest"},
        api_key, client,
    )
    return data.get("result", "0") if data.get("status") == "1" else "0"


async def get_token_transfers(
    wallet: str, chain_id: int, api_key: str, client: httpx.AsyncClient,
    limit: int = 500,
) -> list[dict]:
    """Fetch all ERC-20 token transfer events for a wallet."""
    data = await _get(
        {"chainid": chain_id, "module": "account",
         "action": "tokentx", "address": wallet,
         "startblock": 0, "endblock": 99999999,
         "page": 1, "offset": limit, "sort": "desc"},
        api_key, client,
    )
    if data.get("status") == "1":
        return data.get("result", [])
    return []


def _compute_holdings(wallet: str, transfers: list[dict]) -> list[dict]:
    """
    Compute current token holdings by summing inbound - outbound transfers.
    Returns list of tokens where net balance > 0.
    """
    wallet_lc = wallet.lower()
    balances: dict[str, dict] = {}     # contractAddress → metadata
    net: dict[str, int] = defaultdict(int)

    for tx in transfers:
        contract = tx.get("contractAddress", "").lower()
        if not contract:
            continue

        # Store metadata from the most recent tx for this token
        if contract not in balances:
            balances[contract] = {
                "contract_address": contract,
                "token_name":       tx.get("tokenName", ""),
                "token_symbol":     tx.get("tokenSymbol", ""),
                "decimals":         int(tx.get("tokenDecimal", "18") or "18"),
            }

        value = int(tx.get("value", "0") or "0")
        if tx.get("to", "").lower() == wallet_lc:
            net[contract] += value    # received
        elif tx.get("from", "").lower() == wallet_lc:
            net[contract] -= value    # sent

    holdings = []
    for contract, meta in balances.items():
        balance_raw = net[contract]
        if balance_raw <= 0:
            continue                  # no longer holding
        decimals = meta["decimals"]
        balance  = balance_raw / (10 ** decimals)
        holdings.append({
            **meta,
            "balance_raw": balance_raw,
            "balance":     round(balance, 6),
        })

    return sorted(holdings, key=lambda x: x["balance_raw"], reverse=True)


async def fetch_wallet_holdings(
    wallet: str,
    chain_id: int,
    chain_key: str,
    api_key: str,
) -> dict:
    """
    Fetch all current ERC-20 holdings + native balance for a wallet.
    Returns full wallet snapshot.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        transfers_task = get_token_transfers(wallet, chain_id, api_key, client)
        balance_task   = get_native_balance(wallet, chain_id, api_key, client)

        transfers, native_raw = await asyncio.gather(
            transfers_task, balance_task, return_exceptions=True
        )

    if isinstance(transfers, Exception):
        transfers = []
    if isinstance(native_raw, Exception):
        native_raw = "0"

    holdings = _compute_holdings(wallet, transfers)

    native_balance = int(native_raw) / 1e18

    return {
        "wallet":         wallet.lower(),
        "chain":          chain_key,
        "chain_id":       chain_id,
        "native_balance": round(native_balance, 6),
        "token_count":    len(holdings),
        "holdings":       holdings,
        "transfer_count": len(transfers),
    }
