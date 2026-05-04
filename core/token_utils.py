# core/token_utils.py
"""
Solana token utilities for $AEGIS balance checking and price fetching.
"""
import asyncio
import httpx
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from spl.token.instructions import get_associated_token_address

AEGIS_MINT = "54YCMrqbdrPxiGYaByD1dPNJCWJN3R2zJ2jwPcGpump"
RPC_URL = "https://api.mainnet-beta.solana.com"
JUPITER_PRICE_API = "https://price.jup.ag/v4/price"


async def get_token_balance(wallet: str) -> int:
    """Return raw $AEGIS balance for a wallet (with 6 decimals)."""
    try:
        wallet_pubkey = Pubkey.from_string(wallet)
        mint_pubkey = Pubkey.from_string(AEGIS_MINT)
        token_account = get_associated_token_address(wallet_pubkey, mint_pubkey)
        async with AsyncClient(RPC_URL, commitment="confirmed") as client:
            resp = await client.get_token_account_balance(token_account)
            if resp.value is None:
                return 0
            return int(resp.value.amount)
    except Exception:
        return 0


async def get_token_price() -> float:
    """Fetch $AEGIS price in USD from Jupiter API."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(JUPITER_PRICE_API, params={"ids": AEGIS_MINT})
            data = resp.json()
            price_data = data.get("data", {}).get(AEGIS_MINT, {})
            return float(price_data.get("price", 0))
    except Exception:
        return 0.0


def format_balance(raw: int, decimals: int = 6) -> float:
    """Convert raw balance to human‑readable amount."""
    return raw / (10 ** decimals)


def check_balance_sufficient(balance_raw: int, threshold: int = 100_000_000_000) -> bool:
    """Check if balance meets the 100,000 $AEGIS threshold."""
    return balance_raw >= threshold