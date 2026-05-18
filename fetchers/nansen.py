"""
Nansen API client for wallet labeling, smart money detection, and deployer reputation.
Provides institutional context and holder quality analysis.
"""
import httpx
from typing import Optional, List, Dict, Any
from utils.rate_limiter import RateLimiter
from utils.validators import is_valid_address

# Nansen rate limiting: 100 requests per minute per tier
nansen_limiter = RateLimiter(calls_per_second=1.5)

NANSEN_BASE = "https://api.nansen.ai/api/v1"

# Supported chains for Nansen
NANSEN_CHAINS = {
    "eth": "ethereum",
    "bsc": "binance-smart-chain",
    "polygon": "polygon",
    "arb": "arbitrum",
    "base": "base",
    "op": "optimism",
    "avax": "avalanche",
    "fantom": "fantom",
    "solana": "solana",
}


class NansenClient:
    def __init__(self, api_key: str, timeout: int = 15):
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict:
        return {
            "apikey": self.api_key,
            "Content-Type": "application/json",
        }

    async def get_wallet_label(self, address: str, chain: str) -> Optional[Dict[str, Any]]:
        """
        Fetch wallet label and metadata from Nansen.
        Returns: {
            "label": str,           # e.g., "Binance: Hot Wallet", "Uniswap: Router", etc.
            "entity": str,          # Entity type: "exchange", "fund", "market_maker", "developer", "investor", "unknown"
            "risk_score": float,    # 0-10 entity risk
            "category": str,        # Detailed category
            "is_smart_money": bool, # Whether labeled as smart money
        }
        """
        if not self.api_key or not is_valid_address(address):
            return None

        chain_name = NANSEN_CHAINS.get(chain.lower())
        if not chain_name:
            return None

        await nansen_limiter.acquire()

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                url = f"{NANSEN_BASE}/wallet/{address}/labels"
                params = {"chain": chain_name}
                r = await client.get(url, params=params, headers=self._headers())

                if r.status_code == 200:
                    data = r.json()
                    if data.get("data"):
                        return self._parse_label_response(data["data"])
        except Exception:
            pass

        return None

    async def get_wallet_history(self, address: str, chain: str) -> Optional[Dict[str, Any]]:
        """
        Fetch wallet transaction history and behavior patterns.
        Returns: {
            "transaction_count": int,
            "first_transaction": str,  # ISO timestamp
            "last_transaction": str,   # ISO timestamp
            "total_volume": float,     # In USD
            "success_rate": float,     # 0-1
            "is_active": bool,
        }
        """
        if not self.api_key or not is_valid_address(address):
            return None

        chain_name = NANSEN_CHAINS.get(chain.lower())
        if not chain_name:
            return None

        await nansen_limiter.acquire()

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                url = f"{NANSEN_BASE}/wallet/{address}/history"
                params = {"chain": chain_name}
                r = await client.get(url, params=params, headers=self._headers())

                if r.status_code == 200:
                    data = r.json()
                    if data.get("data"):
                        return self._parse_history_response(data["data"])
        except Exception:
            pass

        return None

    async def get_trending_tokens(self, chain: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Fetch top trending tokens based on smart money activity.
        """
        if not self.api_key:
            return []

        chain_name = NANSEN_CHAINS.get(chain.lower())
        if not chain_name:
            return []

        await nansen_limiter.acquire()

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                url = f"{NANSEN_BASE}/analytics/trending-tokens"
                params = {"chain": chain_name, "limit": limit}
                r = await client.get(url, params=params, headers=self._headers())

                if r.status_code == 200:
                    data = r.json()
                    if data.get("data"):
                        return self._parse_trending_response(data["data"])
        except Exception:
            pass

        return []

    async def get_smart_money_activity(self, contract_address: str, chain: str) -> Optional[Dict[str, Any]]:
        """
        Check if smart money wallets are holding/trading this contract.
        Returns: {
            "smart_money_count": int,           # Number of labeled smart money wallets
            "total_value_held": float,          # USD value held by smart money
            "average_entry_price": float,       # Average price smart money bought at
            "estimated_return": float,          # % return
            "is_accumulating": bool,            # True if buying, False if selling
            "top_holders": [                    # Top smart money holders
                {
                    "address": str,
                    "label": str,
                    "balance": float,
                    "entry_price": float,
                    "return_pct": float,
                }
            ]
        }
        """
    async def get_smart_money_activity(self, contract_address: str, chain: str, debug: bool = False) -> Optional[dict]:
        """Fetch smart money signals via Indicators and Flow Intelligence endpoints."""
        if not self.api_key or not is_valid_address(contract_address):
            return None

        chain_name = NANSEN_CHAINS.get(chain.lower(), chain.lower())
        await nansen_limiter.acquire()
        
        sm_count = 0
        is_accumulating = False
        signals = []

        # 1. Indicators
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                url = f"{NANSEN_BASE}/tgm/indicators"
                payload = {"chain": chain_name, "token_address": contract_address}
                r = await client.post(url, json=payload, headers=self._headers())
                if r.status_code == 200:
                    data = r.json()
                    risk = data.get("risk_indicators", [])
                    reward = data.get("reward_indicators", [])
                    for ind in risk + reward:
                        ind_type = ind.get('indicator_type', '')
                        score = ind.get('score', '').upper()
                        signals.append(f"{ind_type.replace('-', ' ').title()}: {score}")
                        if debug:
                            print(f"[DEBUG] Nansen Indicator: {ind_type} = {score}")
        except Exception as e:
            if debug: print(f"[DEBUG] Indicators Exception: {e}")

        # 2. Flow Intelligence (The primary source for Smart Money count)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                url = f"{NANSEN_BASE}/tgm/flow-intelligence"
                payload = {"chain": chain_name, "token_address": contract_address, "timeframe": "1d"}
                r = await client.post(url, json=payload, headers=self._headers())
                if r.status_code == 200:
                    flow_data = r.json().get("data", [{}])[0]
                    sm_count = flow_data.get("smart_trader_wallet_count", 0)
                    net_flow = flow_data.get("smart_trader_net_flow_usd", 0)
                    is_accumulating = net_flow > 0
                    
                    if sm_count > 0:
                        signals.append(f"Institutional Net Flow: ${net_flow:,.0f}")
                        if debug:
                            print(f"[DEBUG] Nansen Smart Traders: {sm_count}, Net Flow: {net_flow}")
        except Exception as e:
            if debug: print(f"[DEBUG] Flow Intelligence Exception: {e}")

        return {
            "smart_money_count": sm_count,
            "is_accumulating": is_accumulating,
            "signals": signals
        }

    async def get_holder_composition(self, contract_address: str, chain: str, debug: bool = False) -> Optional[Dict[str, Any]]:
        """Fetch holder distribution via TGM Holders endpoint."""
        if not self.api_key or not is_valid_address(contract_address):
            return None

        chain_name = NANSEN_CHAINS.get(chain.lower(), chain.lower())
        await nansen_limiter.acquire()
        
        async def _fetch_holders(premium: bool):
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                url = f"{NANSEN_BASE}/tgm/holders"
                payload = {
                    "chain": chain_name,
                    "token_address": contract_address,
                    "label_type": "all_holders",
                    "pagination": {"page": 1, "per_page": 20},
                    "premium_labels": premium
                }
                return await client.post(url, json=payload, headers=self._headers())

        try:
            r = await _fetch_holders(True)
            if r.status_code == 403:
                if debug: print(f"[DEBUG] Holders 403 - Retrying without premium labels")
                r = await _fetch_holders(False)

            if r.status_code != 200:
                return None

            data = r.json()
            holders = data.get("data", [])
            
            # Calculate composition from top holders
            sm_holders = [h for h in holders if "Smart Money" in (h.get("address_label") or "")]
            fund_holders = [h for h in holders if any(x in (h.get("address_label") or "") for x in ["Fund", "VC", "Capital", "Institutional"])]
            
            return {
                "top_10_concentration_pct": sum(h.get("ownership_percentage", 0) for h in holders[:10]),
                "smart_money_pct": sum(h.get("ownership_percentage", 0) for h in sm_holders),
                "fund_pct": sum(h.get("ownership_percentage", 0) for h in fund_holders),
                "institutional_quality": "high" if (len(sm_holders) > 2 or len(fund_holders) > 0) else "medium" if sm_holders else "low"
            }
        except Exception as e:
            if debug: print(f"[DEBUG] Holders Exception: {e}")
            pass

        return None

    async def get_token_information(self, contract_address: str, chain: str, debug: bool = False) -> Optional[dict]:
        """Fetch basic token information and spot metrics."""
        if not self.api_key or not is_valid_address(contract_address):
            return None

        chain_name = NANSEN_CHAINS.get(chain.lower(), chain.lower())
        await nansen_limiter.acquire()
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                url = f"{NANSEN_BASE}/tgm/token-information"
                payload = {
                    "chain": chain_name,
                    "token_address": contract_address,
                    "timeframe": "1d"
                }
                r = await client.post(url, json=payload, headers=self._headers())

                if r.status_code == 200:
                    data = r.json()
                    metrics = data.get("data", {}).get("spot_metrics", {})
                    return {
                        "total_holders": metrics.get("total_holders", 0),
                        "liquidity_usd": metrics.get("liquidity_usd", 0),
                        "volume_24h": metrics.get("volume_total_usd", 0)
                    }
        except Exception as e:
            print(f"[DEBUG] Nansen Token Info Exception: {e}")
            pass

        return None

    async def get_deployer_reputation(self, deployer_address: str) -> Optional[Dict[str, Any]]:
        """
        Fetch deployer historical reputation and track record.
        Returns: {
            "total_contracts_deployed": int,
            "success_rate": float,              # 0-1
            "failed_contracts": int,            # Rugs, honeypots, etc.
            "total_deployed_value": float,      # USD value deployed
            "is_known_scammer": bool,
            "scam_confidence": float,           # 0-1
            "platforms_used": [str],            # e.g., ["Uniswap", "Pancakeswap"]
            "chains_used": [str],
            "last_activity": str,               # ISO timestamp
            "reputation_score": float,          # 0-10
        }
        """
        if not self.api_key or not is_valid_address(deployer_address):
            return None

        await nansen_limiter.acquire()

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                url = f"{NANSEN_BASE}/address/{deployer_address}/reputation"
                r = await client.get(url, headers=self._headers())

                if r.status_code == 200:
                    data = r.json()
                    if data.get("data"):
                        return self._parse_reputation_response(data["data"])
        except Exception:
            pass

        return None

    # Response parsers
    def _parse_label_response(self, data: Dict) -> Dict[str, Any]:
        """Parse wallet label response."""
        try:
            entity_type = data.get("entity_type", "unknown").lower()
            if entity_type not in ["exchange", "fund", "market_maker", "developer", "investor"]:
                entity_type = "unknown"

            return {
                "label": data.get("label", ""),
                "entity": entity_type,
                "risk_score": float(data.get("risk_score", 5.0)),
                "category": data.get("category", ""),
                "is_smart_money": data.get("is_smart_money", False),
                "nansen_available": True,
            }
        except Exception:
            return {"nansen_available": False}

    def _parse_history_response(self, data: Dict) -> Dict[str, Any]:
        """Parse wallet history response."""
        try:
            return {
                "transaction_count": int(data.get("transaction_count", 0)),
                "first_transaction": data.get("first_transaction", ""),
                "last_transaction": data.get("last_transaction", ""),
                "total_volume": float(data.get("total_volume_usd", 0)),
                "success_rate": float(data.get("success_rate", 1.0)),
                "is_active": data.get("is_active", True),
            }
        except Exception:
            return {}

    def _parse_smart_money_response(self, data: Dict) -> Dict[str, Any]:
        """Parse smart money activity response."""
        try:
            top_holders = []
            for holder in data.get("top_holders", [])[:10]:
                top_holders.append({
                    "address": holder.get("address", ""),
                    "label": holder.get("label", ""),
                    "balance": float(holder.get("balance", 0)),
                    "entry_price": float(holder.get("entry_price", 0)),
                    "return_pct": float(holder.get("return_pct", 0)),
                })

            return {
                "smart_money_count": int(data.get("smart_money_count", 0)),
                "total_value_held": float(data.get("total_value_held_usd", 0)),
                "average_entry_price": float(data.get("average_entry_price", 0)),
                "estimated_return": float(data.get("estimated_return_pct", 0)),
                "is_accumulating": data.get("is_accumulating", False),
                "top_holders": top_holders,
            }
        except Exception:
            return {}

    def _parse_composition_response(self, data: Dict) -> Dict[str, Any]:
        """Parse holder composition response."""
        try:
            quality = "low"
            smart_money_pct = float(data.get("smart_money_pct", 0))
            institutional_pct = float(data.get("fund_pct", 0)) + float(data.get("market_maker_pct", 0))

            if smart_money_pct > 30 or institutional_pct > 25:
                quality = "high"
            elif smart_money_pct > 10 or institutional_pct > 10:
                quality = "medium"

            return {
                "total_holders": int(data.get("total_holders", 0)),
                "smart_money_pct": smart_money_pct,
                "exchange_pct": float(data.get("exchange_pct", 0)),
                "fund_pct": float(data.get("fund_pct", 0)),
                "market_maker_pct": float(data.get("market_maker_pct", 0)),
                "retail_pct": float(data.get("retail_pct", 0)),
                "top_10_concentration_pct": float(data.get("top_10_concentration_pct", 0)),
                "institutional_quality": quality,
            }
        except Exception:
            return {}

    def _parse_reputation_response(self, data: Dict) -> Dict[str, Any]:
        """Parse deployer reputation response."""
        try:
            return {
                "total_contracts_deployed": int(data.get("total_contracts_deployed", 0)),
                "success_rate": float(data.get("success_rate", 0.5)),
                "failed_contracts": int(data.get("failed_contracts", 0)),
                "total_deployed_value": float(data.get("total_deployed_value_usd", 0)),
                "is_known_scammer": data.get("is_known_scammer", False),
                "scam_confidence": float(data.get("scam_confidence", 0)),
                "platforms_used": data.get("platforms_used", []),
                "chains_used": data.get("chains_used", []),
                "last_activity": data.get("last_activity", ""),
                "reputation_score": float(data.get("reputation_score", 5.0)),
            }
        except Exception:
            return {}

    def _parse_trending_response(self, data: List[Dict]) -> List[Dict[str, Any]]:
        """Parse trending tokens response."""
        results = []
        try:
            for item in data:
                results.append({
                    "address": item.get("address", ""),
                    "name": item.get("name", "Unknown"),
                    "symbol": item.get("symbol", "???"),
                    "smart_money_count": int(item.get("smart_money_count", 0)),
                    "change_1h": float(item.get("change_1h_pct", 0)),
                    "change_24h": float(item.get("change_24h_pct", 0)),
                    "liquidity_usd": float(item.get("liquidity_usd", 0)),
                    "institutional_quality": item.get("institutional_quality", "low"),
                })
        except Exception:
            pass
        return results


async def fetch_nansen_deployer_data(
    deployer_address: str, chain: str, client: Optional[NansenClient] = None, debug: bool = False
) -> dict:
    """
    Fetch Nansen deployer reputation and wallet label.
    """
    if not client:
        return {}

    result = {}

    # Get deployer label
    label_data = await client.get_wallet_label(deployer_address, chain)
    if label_data:
        result["label"] = label_data
        if debug:
            print(f"[DEBUG] Nansen deployer label: {label_data.get('label')}")

    # Get deployer reputation
    reputation = await client.get_deployer_reputation(deployer_address)
    if reputation:
        result["reputation"] = reputation
        if debug:
            print(f"[DEBUG] Nansen deployer reputation: {reputation.get('reputation_score')}")

    return result


async def fetch_nansen_contract_data(
    contract_address: str, chain: str, client: Optional[NansenClient] = None, debug: bool = False
) -> dict:
    """
    Fetch Nansen data about a contract (smart money, holders).
    """
    if not client:
        return {}

    result = {}

    # Get smart money activity
    smart_money = await client.get_smart_money_activity(contract_address, chain, debug=debug)
    if smart_money:
        result["smart_money"] = smart_money
        if debug:
            print(f"[DEBUG] Nansen smart money count: {smart_money.get('smart_money_count')}")

    # Get holder composition
    composition = await client.get_holder_composition(contract_address, chain, debug=debug)
    if composition:
        result["composition"] = composition
        if debug:
            print(f"[DEBUG] Nansen holder composition: {composition.get('institutional_quality')}")
    else:
        # Fallback to basic token info if holders endpoint fails (tier restriction)
        token_info = await client.get_token_information(contract_address, chain, debug=debug)
        if token_info:
            result["composition"] = {
                "total_holders": token_info.get("total_holders"),
                "institutional_quality": "low",
                "is_fallback": True
            }
            if debug:
                print(f"[DEBUG] Nansen fallback token info used (holders 403/error)")

    return result
