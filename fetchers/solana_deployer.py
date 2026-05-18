# fetchers/solana_deployer.py
import asyncio
import time
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from solders.pubkey import Pubkey
from aegis_solana.rpc_client import SolanaRPCClient

class SolanaDeployerFetcher:
    def __init__(self, rpc_client: SolanaRPCClient, debug: bool = False):
        self.rpc_client = rpc_client
        self.debug = debug

    async def get_deployment_history(self, deployer: str, limit: int = 150) -> List[dict]:
        """Fetch SPL token & Pump.fun launches for a Solana wallet address."""
        if self.debug:
            print(f"[DEBUG] Fetching Solana deployments for {deployer}")

        deployments = []
        seen_mints = set()

        try:
            # 1. Get last signatures
            signatures_result = await self.rpc_client._make_rpc_request(
                "getSignaturesForAddress",
                [deployer, {"limit": limit}]
            )
            if not signatures_result:
                return []

            tx_signatures = [s["signature"] for s in signatures_result]
            if self.debug:
                print(f"[DEBUG] Found {len(tx_signatures)} signatures, fetching parsed transactions...")

            # 2. Fetch parsed transactions in batches of 15 to avoid rate-limiting/timeouts
            batch_size = 15
            parsed_txs = []
            for i in range(0, len(tx_signatures), batch_size):
                batch_sigs = tx_signatures[i:i + batch_size]
                tasks = [
                    self.rpc_client._make_rpc_request(
                        "getTransaction",
                        [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
                    )
                    for sig in batch_sigs
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for res in results:
                    if not isinstance(res, Exception) and res:
                        parsed_txs.append(res)

            if self.debug:
                print(f"[DEBUG] Successfully parsed {len(parsed_txs)} transactions, scanning instructions...")

            # 3. Scan for token creation
            for tx in parsed_txs:
                block_time = tx.get("blockTime")
                date_str = datetime.fromtimestamp(block_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if block_time else "unknown"
                ts = block_time or int(time.time())

                transaction = tx.get("transaction", {})
                message = transaction.get("message", {})
                instructions = message.get("instructions", [])

                meta = tx.get("meta", {}) or {}
                inner_instructions = meta.get("innerInstructions", []) or []
                
                all_instructions = list(instructions)
                for inner in inner_instructions:
                    all_instructions.extend(inner.get("instructions", []))

                for inst in all_instructions:
                    program = inst.get("program")
                    parsed = inst.get("parsed")
                    program_id = inst.get("programId")

                    # Standard SPL Token initialization
                    if program in ["spl-token", "spl-token-2022"] and parsed:
                        inst_type = parsed.get("type")
                        if inst_type in ["initializeMint", "initializeMint2"]:
                            info = parsed.get("info", {})
                            mint = info.get("mint")
                            if mint and mint not in seen_mints:
                                seen_mints.add(mint)
                                deployments.append({
                                    "contract_address": mint,
                                    "tx_hash": transaction.get("signatures", [""])[0],
                                    "timestamp": ts,
                                    "date": date_str,
                                    "chain": "solana",
                                    "verified": True,
                                    "token_name": "",
                                    "token_symbol": "",
                                    "holder_count": 0,
                                    "launch_type": "Standard SPL Token"
                                })

                    # Pump.fun launch detection
                    elif program_id == "6EF8rrecth7KVndstV7tvnJGD9VScTzF2khKV37tBi1H":
                        accounts = inst.get("accounts", [])
                        if accounts and len(accounts) >= 2:
                            mint = accounts[0]
                            if mint.endswith("pump") and mint not in seen_mints:
                                seen_mints.add(mint)
                                deployments.append({
                                    "contract_address": mint,
                                    "tx_hash": transaction.get("signatures", [""])[0],
                                    "timestamp": ts,
                                    "date": date_str,
                                    "chain": "solana",
                                    "verified": True,
                                    "token_name": "",
                                    "token_symbol": "",
                                    "holder_count": 0,
                                    "launch_type": "Pump.fun Launch"
                                })

            # Sort deployments (newest first for enrichment)
            deployments.sort(key=lambda x: x["timestamp"], reverse=True)
            if self.debug:
                print(f"[DEBUG] Found {len(deployments)} total Solana deployments before enrichment")

            # Enrich top 15 most recent deployments with metadata and holders
            recent = deployments[:15]
            if recent:
                if self.debug:
                    print(f"[DEBUG] Enriching {len(recent)} recent deployments...")
                
                enrich_tasks = []
                for d in recent:
                    mint_pubkey = Pubkey.from_string(d["contract_address"])
                    enrich_tasks.append(asyncio.gather(
                        self.rpc_client.get_token_metadata(mint_pubkey),
                        self.rpc_client.get_token_largest_holders(mint_pubkey, limit=10),
                        return_exceptions=True
                    ))
                
                enrich_results = await asyncio.gather(*enrich_tasks, return_exceptions=True)
                for idx, result in enumerate(enrich_results):
                    if isinstance(result, Exception) or not result:
                        continue
                    meta, holders = result if len(result) == 2 else ({}, [])
                    if not isinstance(meta, Exception) and meta:
                        recent[idx]["token_name"] = meta.get("name") or "Unknown SPL Token"
                        recent[idx]["token_symbol"] = meta.get("symbol") or "SPL"
                    if not isinstance(holders, Exception) and holders:
                        recent[idx]["holder_count"] = len(holders)

                # Update main list
                for idx, enriched_item in enumerate(recent):
                    deployments[idx] = enriched_item

        except Exception as e:
            if self.debug:
                print(f"[DEBUG] Solana deployments fetch failed: {e}")

        # Return oldest first to match EVM logic
        deployments.reverse()
        return deployments

    async def analyze_funder(self, deployer: str, deployments: List[dict]) -> Dict[str, Any]:
        """Analyze native SOL funding transaction/source for the deployer address."""
        try:
            signatures_result = await self.rpc_client._make_rpc_request(
                "getSignaturesForAddress",
                [deployer, {"limit": 1000}]
            )
            if not signatures_result:
                return {"funder_address": "unknown", "funding_tx": "", "funding_date": "", "funding_value_eth": 0.0}

            # Oldest signature first
            oldest_sig = signatures_result[-1]["signature"]
            tx = await self.rpc_client._make_rpc_request(
                "getTransaction",
                [oldest_sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
            )
            if tx:
                block_time = tx.get("blockTime")
                date_str = datetime.fromtimestamp(block_time, tz=timezone.utc).strftime("%Y-%m-%d") if block_time else "unknown"
                transaction = tx.get("transaction", {})
                message = transaction.get("message", {})
                
                # Look for transfer instruction
                instructions = message.get("instructions", [])
                for inst in instructions:
                    if inst.get("program") == "system" and inst.get("parsed", {}).get("type") == "transfer":
                        info = inst.get("parsed", {}).get("info", {})
                        if info.get("destination") == deployer:
                            return {
                                "funder_address": info.get("source", "unknown"),
                                "funding_tx": oldest_sig,
                                "funding_date": date_str,
                                "funding_value_eth": info.get("lamports", 0) / 1e9, # SOL
                            }
        except Exception:
            pass

        return {"funder_address": "unknown", "funding_tx": "", "funding_date": "", "funding_value_eth": 0.0}

    def calculate_risk_profile(self, deployments: List[dict], funder_info: Dict[str, Any]) -> Dict[str, Any]:
        total = len(deployments)
        if total == 0:
            return {
                "reputation_score": 100,
                "multi_chain": False,
                "unverified_ratio": 0.0,
                "low_holder_ratio": 0.0,
                "rapid_burst": False,
                "risk_flags": [],
            }

        low_holders = sum(1 for d in deployments if d.get("holder_count", 0) < 5)
        timestamps = [d["timestamp"] for d in deployments if d["timestamp"] > 0]
        rapid_burst = False
        if len(timestamps) >= 3:
            for i in range(len(timestamps) - 2):
                if timestamps[i + 2] - timestamps[i] < 86400: # 3 tokens in 24 hours
                    rapid_burst = True
                    break

        score = 100
        if low_holders > 0:
            score -= min(30, low_holders * 10)
        if rapid_burst:
            score -= 25
        score = max(0, min(100, score))

        flags = []
        if low_holders > 0:
            flags.append(f"{low_holders} low-adoption Solana tokens")
        if rapid_burst:
            flags.append("Rapid deployment burst")

        return {
            "reputation_score": score,
            "multi_chain": False,
            "unverified_ratio": 0.0,
            "low_holder_ratio": low_holders / total if total else 0,
            "rapid_burst": rapid_burst,
            "risk_flags": flags,
        }
