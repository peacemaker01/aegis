# fetchers/deployer.py
"""
Deployer Forensics – Direct Detection from TxList
"""
import asyncio
from datetime import datetime, timezone
import httpx

from core.chains import CHAINS
from utils.rate_limiter import RateLimiter

deployer_limiter = RateLimiter(calls_per_second=2)
BASE = "https://api.etherscan.io/v2/api"

# ----------------------------------------------------------------------
# 1. Fetch transaction list for a wallet
# ----------------------------------------------------------------------
async def _fetch_txlist(
    address: str,
    chain_id: int,
    api_key: str,
    client: httpx.AsyncClient,
    limit: int = 500,
) -> list[dict]:
    await deployer_limiter.acquire()
    params = {
        "chainid": chain_id,
        "module": "account",
        "action": "txlist",
        "address": address,
        "startblock": 0,
        "endblock": 99999999,
        "page": 1,
        "offset": limit,
        "sort": "desc",
        "apikey": api_key,
    }
    try:
        r = await client.get(BASE, params=params, timeout=30)
        data = r.json()
        if data.get("status") == "1":
            return data.get("result", [])
    except Exception:
        pass
    return []

# ----------------------------------------------------------------------
# 2. Extract contract creations from txlist
# ----------------------------------------------------------------------
def _extract_deployments(txs: list[dict], chain_key: str) -> list[dict]:
    """Extract deployments using contractAddress field + to field detection."""
    deployments = []
    zero_addr = "0x0000000000000000000000000000000000000000"
    for tx in txs:
        contract_addr = tx.get("contractAddress", "")
        if not contract_addr or len(contract_addr) < 10:
            continue
        to_field = tx.get("to", "").strip()
        # Creation detection: to is empty, "0x", or zero address
        is_creation = to_field in ("", "0x", zero_addr)
        if not is_creation:
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
            "gas_used": tx.get("gasUsed", "0"),
            "is_error": tx.get("isError", "0") == "1",
        })
    return deployments

# ----------------------------------------------------------------------
# 3. Enrich contract with metadata (verified, token info)
# ----------------------------------------------------------------------
async def _enrich_contract(
    deployment: dict,
    api_key: str,
    client: httpx.AsyncClient,
) -> dict:
    address = deployment["contract_address"]
    chain_id = deployment["chain_id"]

    # Source code (verified, contract name)
    await deployer_limiter.acquire()
    params = {
        "chainid": chain_id,
        "module": "contract",
        "action": "getsourcecode",
        "address": address,
        "apikey": api_key,
    }
    try:
        r = await client.get(BASE, params=params, timeout=20)
        data = r.json()
        if data.get("status") == "1" and data.get("result"):
            res = data["result"][0] if isinstance(data["result"], list) else data["result"]
            deployment["verified"] = bool(res.get("SourceCode", "").strip())
            deployment["contract_name"] = res.get("ContractName", "")
            deployment["compiler"] = res.get("CompilerVersion", "")
            deployment["license"] = res.get("LicenseType", "None")
            deployment["proxy"] = res.get("Proxy", "0") == "1"
        else:
            deployment["verified"] = False
            deployment["contract_name"] = ""
    except Exception:
        deployment["verified"] = False
        deployment["contract_name"] = ""

    # Token info (name, symbol, holders)
    await deployer_limiter.acquire()
    params2 = {
        "chainid": chain_id,
        "module": "token",
        "action": "tokeninfo",
        "contractaddress": address,
        "apikey": api_key,
    }
    try:
        r2 = await client.get(BASE, params=params2, timeout=15)
        d2 = r2.json()
        if d2.get("status") == "1" and d2.get("result"):
            ti = d2["result"][0] if isinstance(d2["result"], list) else d2["result"]
            deployment["token_name"] = ti.get("tokenName", "")
            deployment["token_symbol"] = ti.get("symbol", "")
            deployment["total_supply"] = ti.get("totalSupply", "")
            deployment["holder_count"] = ti.get("holdersCount", "0")
        else:
            deployment["token_name"] = ""
            deployment["token_symbol"] = ""
            deployment["holder_count"] = "0"
    except Exception:
        deployment["token_name"] = ""
        deployment["token_symbol"] = ""
        deployment["holder_count"] = "0"

    return deployment

# ----------------------------------------------------------------------
# 4. Fetch funder (first inbound transaction)
# ----------------------------------------------------------------------
async def _fetch_funder_on_chain(
    deployer: str,
    chain_id: int,
    api_key: str,
    client: httpx.AsyncClient,
) -> dict:
    await deployer_limiter.acquire()
    params = {
        "chainid": chain_id,
        "module": "account",
        "action": "txlist",
        "address": deployer,
        "startblock": 0,
        "endblock": 99999999,
        "page": 1,
        "offset": 50,
        "sort": "asc",
        "apikey": api_key,
    }
    try:
        r = await client.get(BASE, params=params, timeout=20)
        data = r.json()
        if data.get("status") == "1" and data.get("result"):
            for tx in data["result"]:
                if tx.get("to", "").lower() == deployer.lower():
                    return {
                        "funder_address": tx.get("from", ""),
                        "funding_tx": tx.get("hash", ""),
                        "funding_date": datetime.fromtimestamp(
                            int(tx.get("timeStamp", 0)), tz=timezone.utc
                        ).strftime("%Y-%m-%d"),
                        "funding_value_eth": str(int(tx.get("value", "0")) / 1e18)[:8],
                    }
    except Exception:
        pass
    return {"funder_address": "unknown", "funding_tx": "", "funding_date": "", "funding_value_eth": ""}

# ----------------------------------------------------------------------
# 5. Main entry point
# ----------------------------------------------------------------------
async def fetch_deployer_profile(
    deployer: str,
    api_key: str,
    chains_to_scan: list[str] | None = None,
    enrich: bool = True,
) -> dict:
    if chains_to_scan is None:
        chains_to_scan = ["eth", "bsc", "polygon", "base", "arb"]

    async with httpx.AsyncClient(timeout=60) as client:
        # Fetch tx lists for all chains concurrently
        tx_tasks = {
            chain: _fetch_txlist(deployer, CHAINS[chain]["id"], api_key, client)
            for chain in chains_to_scan if chain in CHAINS
        }
        tx_results = await asyncio.gather(*tx_tasks.values(), return_exceptions=True)
        chain_txs = dict(zip(tx_tasks.keys(), tx_results))
        for chain in chain_txs:
            if isinstance(chain_txs[chain], Exception):
                chain_txs[chain] = []

        # Extract deployments from each chain
        all_deployments = []
        for chain, txs in chain_txs.items():
            all_deployments.extend(_extract_deployments(txs, chain))

        # Sort newest first
        all_deployments.sort(key=lambda x: x["timestamp"], reverse=True)

        # Enrich with contract metadata (limit to 20)
        if enrich and all_deployments:
            enrich_tasks = [_enrich_contract(d, api_key, client) for d in all_deployments[:20]]
            enriched = await asyncio.gather(*enrich_tasks, return_exceptions=True)
            for i, result in enumerate(enriched):
                if not isinstance(result, Exception):
                    all_deployments[i] = result

        # Find funder – use chain of first deployment if available, else ETH
        funder = {"funder_address": "unknown", "funding_tx": "", "funding_date": "", "funding_value_eth": ""}
        if all_deployments:
            earliest = min(all_deployments, key=lambda x: x["timestamp"])
            funder = await _fetch_funder_on_chain(deployer, earliest["chain_id"], api_key, client)
        if funder.get("funder_address") == "unknown":
            funder = await _fetch_funder_on_chain(deployer, 1, api_key, client)

        # Risk signals
        chains_active = list({d["chain"] for d in all_deployments})
        unverified = [d for d in all_deployments if not d.get("verified", True)]
        low_holders = [d for d in all_deployments if int(d.get("holder_count", "0") or "0") < 50]

        rapid = False
        if len(all_deployments) >= 3:
            timestamps = sorted([d["timestamp"] for d in all_deployments], reverse=True)
            for i in range(len(timestamps) - 2):
                if timestamps[i] - timestamps[i + 2] < 30 * 86400:
                    rapid = True
                    break

        return {
            "deployer": deployer.lower(),
            "total_deployments": len(all_deployments),
            "chains_active": chains_active,
            "chains_scanned": chains_to_scan,
            "funder": funder,
            "deployments": all_deployments,
            "risk_signals": {
                "multi_chain_deployer": len(chains_active) > 1,
                "has_unverified_contracts": len(unverified) > 0,
                "unverified_count": len(unverified),
                "rapid_deployments": rapid,
                "low_holder_contracts": len(low_holders),
                "total_contracts": len(all_deployments),
            },
        }
