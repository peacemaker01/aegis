# fetchers/deployer.py
"""
Deployer Forensics – Cross-chain wallet profiling and risk scoring.
"""
import asyncio
import httpx
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from core.chains import CHAINS
from utils.rate_limiter import RateLimiter

deployer_limiter = RateLimiter(calls_per_second=2)
ETHERSCAN_BASE = "https://api.etherscan.io/v2/api"


class DeployerFetcher:
    def __init__(self, etherscan_keys: List[str], debug: bool = False):
        self.etherscan_keys = etherscan_keys
        self.active_key_index = 0
        self.key_lock = asyncio.Lock()
        self.debug = debug

    async def _get_api_key(self) -> str:
        async with self.key_lock:
            key = self.etherscan_keys[self.active_key_index]
            self.active_key_index = (self.active_key_index + 1) % len(self.etherscan_keys)
            return key

    async def _fetch_txlist(
        self, address: str, chain_id: int, api_key: str, client: httpx.AsyncClient
    ) -> List[dict]:
        await deployer_limiter.acquire()
        params = {
            "chainid": chain_id, "module": "account", "action": "txlist",
            "address": address, "startblock": 0, "endblock": 99999999,
            "page": 1, "offset": 1000, "sort": "asc", "apikey": api_key,
        }
        if self.debug:
            print(f"[DEBUG] Fetching txlist for chain_id={chain_id}...")
        try:
            r = await client.get(ETHERSCAN_BASE, params=params, timeout=30)
            data = r.json()
            if data.get("status") == "1":
                txs = data.get("result", [])
                if self.debug:
                    print(f"[DEBUG]   Got {len(txs)} transactions")
                return txs
            else:
                if self.debug:
                    print(f"[DEBUG]   API error: {data.get('message')}")
        except Exception as e:
            if self.debug:
                print(f"[DEBUG]   Request failed: {e}")
        return []

    def _extract_deployments(self, txs: List[dict], chain_key: str) -> List[dict]:
        deployments = []
        zero_addr = "0x0000000000000000000000000000000000000000"
        for tx in txs:
            contract_addr = tx.get("contractAddress", "")
            if not contract_addr or len(contract_addr) < 10:
                continue
            to_field = tx.get("to", "").strip()
            if to_field not in ("", "0x", zero_addr):
                continue
            ts = int(tx.get("timeStamp", 0))
            deployments.append({
                "contract_address": contract_addr.lower(),
                "tx_hash": tx.get("hash", ""),
                "block": int(tx.get("blockNumber", 0)),
                "timestamp": ts,
                "date": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ts else "unknown",
                "chain": chain_key,
                "chain_id": CHAINS[chain_key]["id"],
                "gas_used": int(tx.get("gasUsed", 0)),
                "is_error": tx.get("isError", "0") == "1",
            })
        return deployments

    async def _enrich_deployment(self, deployment: dict, api_key: str, client: httpx.AsyncClient) -> dict:
        address = deployment["contract_address"]
        chain_id = deployment["chain_id"]

        if self.debug:
            print(f"[DEBUG]   Enriching {address[:10]}... on chain {chain_id}")

        await deployer_limiter.acquire()
        params = {
            "chainid": chain_id, "module": "contract", "action": "getsourcecode",
            "address": address, "apikey": api_key,
        }
        try:
            r = await client.get(ETHERSCAN_BASE, params=params, timeout=20)
            data = r.json()
            if data.get("status") == "1" and data.get("result"):
                res = data["result"][0] if isinstance(data["result"], list) else data["result"]
                deployment["verified"] = bool(res.get("SourceCode", "").strip())
                deployment["contract_name"] = res.get("ContractName", "")
            else:
                deployment["verified"] = False
                deployment["contract_name"] = ""
        except Exception:
            deployment["verified"] = False
            deployment["contract_name"] = ""

        await deployer_limiter.acquire()
        params2 = {
            "chainid": chain_id, "module": "token", "action": "tokeninfo",
            "contractaddress": address, "apikey": api_key,
        }
        try:
            r2 = await client.get(ETHERSCAN_BASE, params=params2, timeout=15)
            d2 = r2.json()
            if d2.get("status") == "1" and d2.get("result"):
                ti = d2["result"][0] if isinstance(d2["result"], list) else d2["result"]
                deployment["token_name"] = ti.get("tokenName", "")
                deployment["token_symbol"] = ti.get("symbol", "")
                deployment["holder_count"] = int(ti.get("holdersCount", "0") or "0")
            else:
                deployment["token_name"] = ""
                deployment["token_symbol"] = ""
                deployment["holder_count"] = 0
        except Exception:
            deployment["token_name"] = ""
            deployment["token_symbol"] = ""
            deployment["holder_count"] = 0

        if self.debug:
            print(f"[DEBUG]     verified={deployment['verified']}, holders={deployment['holder_count']}")

        return deployment

    async def get_deployment_history(self, deployer: str, chains_to_scan: List[str]) -> List[dict]:
        all_deployments = []
        async with httpx.AsyncClient(timeout=60) as client:
            api_key = await self._get_api_key()
            if self.debug:
                print(f"[DEBUG] Using API key: {api_key[:8]}...")

            tx_tasks = {
                chain: self._fetch_txlist(deployer, CHAINS[chain]["id"], api_key, client)
                for chain in chains_to_scan if chain in CHAINS
            }
            tx_results = await asyncio.gather(*tx_tasks.values(), return_exceptions=True)
            chain_txs = dict(zip(tx_tasks.keys(), tx_results))

            for chain, txs in chain_txs.items():
                if isinstance(txs, list):
                    extracted = self._extract_deployments(txs, chain)
                    if self.debug:
                        print(f"[DEBUG] Chain {chain}: extracted {len(extracted)} deployments")
                    all_deployments.extend(extracted)

            all_deployments.sort(key=lambda x: x["timestamp"])
            if self.debug:
                print(f"[DEBUG] Total deployments before enrichment: {len(all_deployments)}")

            if all_deployments:
                recent = all_deployments[-30:]
                if self.debug:
                    print(f"[DEBUG] Enriching {len(recent)} most recent deployments...")
                enrich_tasks = [self._enrich_deployment(d, api_key, client) for d in recent]
                enriched = await asyncio.gather(*enrich_tasks, return_exceptions=True)
                for i, result in enumerate(enriched):
                    if not isinstance(result, Exception):
                        all_deployments[-(len(recent)-i)] = result
                if self.debug:
                    print(f"[DEBUG] Enrichment complete")

        return all_deployments

    async def analyze_funder(self, deployer: str, deployments: List[dict], api_key: str) -> Dict[str, Any]:
        if not deployments:
            if self.debug:
                print("[DEBUG] No deployments, skipping funder analysis")
            return {"funder_address": "unknown", "funding_tx": "", "funding_date": "", "funding_value_eth": 0.0}

        earliest_chain = deployments[0]["chain_id"]
        if self.debug:
            print(f"[DEBUG] Analyzing funder on chain {earliest_chain}...")

        await deployer_limiter.acquire()
        params = {
            "chainid": earliest_chain, "module": "account", "action": "txlist",
            "address": deployer, "startblock": 0, "endblock": 99999999,
            "page": 1, "offset": 100, "sort": "asc", "apikey": api_key,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                r = await client.get(ETHERSCAN_BASE, params=params)
                data = r.json()
                if data.get("status") == "1" and data.get("result"):
                    for tx in data["result"]:
                        if tx.get("to", "").lower() == deployer.lower() and int(tx.get("value", "0")) > 0:
                            funder = {
                                "funder_address": tx.get("from", ""),
                                "funding_tx": tx.get("hash", ""),
                                "funding_date": datetime.fromtimestamp(
                                    int(tx.get("timeStamp", 0)), tz=timezone.utc
                                ).strftime("%Y-%m-%d"),
                                "funding_value_eth": int(tx.get("value", "0")) / 1e18,
                            }
                            if self.debug:
                                print(f"[DEBUG] Found funder: {funder['funder_address']}")
                            return funder
            except Exception as e:
                if self.debug:
                    print(f"[DEBUG] Funder analysis failed: {e}")
        return {"funder_address": "unknown", "funding_tx": "", "funding_date": "", "funding_value_eth": 0.0}

    def calculate_risk_profile(self, deployments: List[dict], funder_info: Dict[str, Any]) -> Dict[str, Any]:
        total = len(deployments)
        if total == 0:
            if self.debug:
                print("[DEBUG] No deployments – clean profile")
            return {
                "reputation_score": 100,
                "multi_chain": False,
                "unverified_ratio": 0.0,
                "low_holder_ratio": 0.0,
                "rapid_burst": False,
                "risk_flags": [],
            }

        chains = set(d["chain"] for d in deployments)
        unverified = sum(1 for d in deployments if not d.get("verified", False))
        low_holders = sum(1 for d in deployments if d.get("holder_count", 0) < 50)

        timestamps = [d["timestamp"] for d in deployments if d["timestamp"] > 0]
        rapid_burst = False
        if len(timestamps) >= 3:
            for i in range(len(timestamps) - 2):
                if timestamps[i + 2] - timestamps[i] < 86400:
                    rapid_burst = True
                    break

        score = 100
        if len(chains) > 1:
            score -= 15
        if unverified > 0:
            score -= min(30, unverified * 5)
        if low_holders > 0:
            score -= min(25, low_holders * 5)
        if rapid_burst:
            score -= 20
        score = max(0, min(100, score))

        flags = []
        if len(chains) > 1:
            flags.append("Multi-chain activity")
        if unverified > 0:
            flags.append(f"{unverified} unverified contracts")
        if low_holders > 0:
            flags.append(f"{low_holders} low-holder tokens")
        if rapid_burst:
            flags.append("Rapid deployment burst")

        if self.debug:
            print(f"[DEBUG] Risk calculation: chains={len(chains)}, unverified={unverified}, low_holders={low_holders}, burst={rapid_burst}")
            print(f"[DEBUG] Final score: {score}")

        return {
            "reputation_score": score,
            "multi_chain": len(chains) > 1,
            "unverified_ratio": unverified / total if total else 0,
            "low_holder_ratio": low_holders / total if total else 0,
            "rapid_burst": rapid_burst,
            "risk_flags": flags,
        }