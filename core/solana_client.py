# core/solana_client.py
import asyncio
import httpx
from typing import Optional, Dict, Any, List
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.signature import Signature
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TokenAccountOpts
from solana.rpc.core import RPCException
from solders.token.associated import get_associated_token_address


class SolanaClient:
    def __init__(self, rpc_url: str):
        self.client = AsyncClient(rpc_url)

    async def close(self):
        await self.client.close()

    async def get_transaction(self, signature: str, encoding: str = "jsonParsed"):
        try:
            sig = Signature.from_string(signature)
            resp = await self.client.get_transaction(
                sig,
                encoding=encoding,
                max_supported_transaction_version=0,
                commitment="confirmed"
            )
            return resp.value
        except RPCException:
            return None

    async def get_token_balance(self, owner: Pubkey, mint: Pubkey) -> int:
        try:
            ata = get_associated_token_address(owner, mint)
            resp = await self.client.get_token_account_balance(ata, commitment="confirmed")
            return resp.value.amount if resp.value else 0
        except RPCException:
            return 0

    async def get_token_price(self, mint: Pubkey) -> Optional[float]:
        """Fetch price from Jupiter API (USD)."""
        url = f"https://quote-api.jup.ag/v6/price?ids={str(mint)}"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=10)
                data = resp.json()
                return float(data['data'][str(mint)]['price'])
        except Exception:
            return None

    async def send_transaction(self, transaction: bytes) -> str:
        resp = await self.client.send_raw_transaction(transaction, opts={"skip_preflight": False})
        return str(resp.value)

    @staticmethod
    def keypair_from_private_key(private_key_b58: str) -> Keypair:
        return Keypair.from_base58_string(private_key_b58)