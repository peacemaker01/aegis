# aegis_solana/rpc_client.py
"""
FluxRPC client for Solana JSON-RPC.
Uses the FluxRPC endpoint with API key as query parameter.
"""
import httpx
from typing import Optional, List, Dict, Any
from solders.pubkey import Pubkey


class SolanaRPCClient:
    def __init__(self, endpoint: str, api_key: Optional[str] = None, debug: bool = False):
        self.endpoint = endpoint.rstrip("/") + "?api-key=" + api_key if api_key and "api-key" not in endpoint else endpoint
        self.api_key = api_key
        self.debug = debug

        if self.debug:
            print(f"[DEBUG] SolanaRPCClient initialized: {self.endpoint}")

    async def _make_rpc_request(self, method: str, params: List[Any]) -> Dict[str, Any]:
        """Make a JSON-RPC request to the FluxRPC endpoint."""
        url = self.endpoint

        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise Exception(f"RPC error: {data['error']}")
            return data["result"]

    async def get_mint_info(self, mint: Pubkey) -> Dict[str, Any]:
        """Fetch mint account info to check authorities."""
        try:
            result = await self._make_rpc_request("getAccountInfo", [
                str(mint),
                {"encoding": "jsonParsed"}
            ])
            value = result.get("value", {})
            if not value:
                return {}
            data = value.get("data", {}).get("parsed", {}).get("info", {})
            return {
                "mint_authority": data.get("mintAuthority"),
                "freeze_authority": data.get("freezeAuthority"),
                "supply": data.get("supply"),
                "decimals": data.get("decimals"),
                "is_initialized": data.get("isInitialized", False),
            }
        except Exception as e:
            if self.debug:
                print(f"[DEBUG] get_mint_info failed: {e}")
            return {}

    async def get_token_metadata(self, mint: Pubkey) -> Dict[str, Any]:
        """Get token name/symbol via Helius DAS getAsset, falling back to getTokenSupply for decimals."""
        # Try DAS API first (Helius-specific – returns name/symbol)
        try:
            result = await self._make_rpc_request("getAsset", [str(mint)])
            if result:
                content = result.get("content", {})
                meta = content.get("metadata", {})
                token_info = result.get("token_info", {})
                name = meta.get("name") or ""
                symbol = meta.get("symbol") or token_info.get("symbol", "")
                decimals = token_info.get("decimals", 9)
                if name or symbol:
                    return {"name": name, "symbol": symbol, "decimals": decimals}
        except Exception:
            pass
        # Fallback: decimals only
        try:
            supply_info = await self._make_rpc_request("getTokenSupply", [str(mint)])
            decimals = supply_info.get("value", {}).get("decimals", 9)
            return {"name": "", "symbol": "", "decimals": decimals}
        except Exception as e:
            if self.debug:
                print(f"[DEBUG] get_token_metadata failed: {e}")
            return {}

    async def get_token_largest_holders(self, mint: Pubkey, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            result = await self._make_rpc_request(
                "getTokenLargestAccounts",
                [str(mint), {"commitment": "confirmed"}]
            )
            holders = result.get("value", [])
            total_supply = sum(int(h.get("amount", "0")) for h in holders)
            enriched = []
            for h in holders[:limit]:
                amount = int(h.get("amount", "0"))
                pct = (amount / total_supply * 100) if total_supply > 0 else 0
                enriched.append({
                    "address": h.get("address", ""),
                    "amount": amount,
                    "ui_amount": amount / (10 ** 9),
                    "percentage": round(pct, 2),
                })
            return enriched
        except Exception as e:
            error_msg = str(e)
            if "Too many accounts requested" in error_msg or "-32600" in error_msg:
                if self.debug:
                    print(f"[DEBUG] Token has too many holder accounts; skipping holder analysis.")
            else:
                if self.debug:
                    print(f"[DEBUG] get_token_largest_holders failed: {e}")
            return []

    async def get_creator_address(self, mint: Pubkey) -> Optional[str]:
        # Not available via standard RPC; return None
        return None


# For backward compatibility
HeliusClient = SolanaRPCClient