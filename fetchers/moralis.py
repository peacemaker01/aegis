# fetchers/moralis.py
import httpx
from typing import Dict, Any, List, Optional


class MoralisClient:
    def __init__(self, api_key: str, debug: bool = False):
        self.api_key = api_key
        self.base_url = "https://deep-index.moralis.io/api/v2.2"
        self.headers = {
            "accept": "application/json",
            "X-API-Key": api_key,
        }
        self.debug = debug

    async def _get(self, endpoint: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        if self.debug:
            print(f"[DEBUG] Moralis: GET {url} params={params}")
        # 90 second timeout for large responses
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.get(url, headers=self.headers, params=params or {})
            if self.debug:
                print(f"[DEBUG] Moralis: response status {resp.status_code}")
                print(f"[DEBUG] Moralis: response body (first 300 chars) {resp.text[:300]}")
            resp.raise_for_status()
            return resp.json()

    async def get_wallet_tokens(self, address: str, chains: Optional[str] = None) -> List[Dict[str, Any]]:
        endpoint = f"wallets/{address}/tokens"
        params = {"exclude_spam": "true", "exclude_unverified_contracts": "true"}
        if chains:
            params["chains"] = chains
        data = await self._get(endpoint, params)
        return data.get("result", [])

    async def get_wallet_net_worth(self, address: str, chains: Optional[str] = None) -> Dict[str, Any]:
        endpoint = f"wallets/{address}/net-worth"
        params = {"exclude_spam": "true", "exclude_unverified_contracts": "true"}
        if chains:
            params["chains"] = chains
        return await self._get(endpoint, params)