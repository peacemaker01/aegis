"""
Etherscan V2 API fetcher.
Single API key → all EVM chains via ?chainid=<id>
Base URL: https://api.etherscan.io/v2/api
"""
import json
import asyncio
import httpx
from typing import Optional, List
from utils.rate_limiter import etherscan_limiter
from utils.api_key_pool import ApiKeyPool

BASE = "https://api.etherscan.io/v2/api"
_etherscan_pool: Optional[ApiKeyPool] = None
ETHERSCAN_DEBUG = False


def init_etherscan_pool(keys: List[str], calls_per_second: float = 5.0):
    """Initialize the Etherscan API key pool."""
    global _etherscan_pool
    if keys:
        _etherscan_pool = ApiKeyPool(keys, calls_per_second)


async def _get(params: dict, api_key: str) -> dict:
    """Throttled GET wrapper using key pool if available."""
    if ETHERSCAN_DEBUG:
        print(f"[DEBUG] Etherscan request: {params}")
    await etherscan_limiter.acquire()
    
    if _etherscan_pool:
        key = await _etherscan_pool.acquire()
    else:
        key = api_key
    
    params["apikey"] = key
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.get(BASE, params=params)
            r.raise_for_status()
            data = r.json()
            if data.get("status") == "0" and "rate limit" in data.get("result", "").lower():
                if _etherscan_pool:
                    _etherscan_pool.report_failure(key, rate_limited=True)
                raise Exception("Rate limited")
            if _etherscan_pool:
                _etherscan_pool.report_success(key)
            return data
        except Exception as e:
            if _etherscan_pool:
                _etherscan_pool.report_failure(key)
            raise e


async def is_contract(address: str, chain_id: int, api_key: str) -> bool:
    """Determine if an address is a smart contract."""
    params = {
        "chainid": chain_id,
        "module": "account",
        "action": "getcode",
        "address": address,
        "apikey": api_key,
    }
    try:
        data = await _get(params, api_key)
        if data.get("status") == "1":
            code = data.get("result", "")
            if code and len(code) > 2 and code != "0x":
                return True
    except Exception:
        pass

    try:
        src = await get_source_code(address, chain_id, api_key)
        if src.get("verified"):
            return True
    except Exception:
        pass

    try:
        creation = await get_contract_creation(address, chain_id, api_key)
        if creation.get("contractCreator"):
            return True
    except Exception:
        pass

    return True


async def get_source_code(address: str, chain_id: int, api_key: str) -> dict:
    data = await _get(
        {"chainid": chain_id, "module": "contract",
         "action": "getsourcecode", "address": address},
        api_key,
    )
    if data.get("status") != "1" or not data.get("result"):
        return {"verified": False, "source": "", "abi": "[]",
                "compiler": "", "proxy": False, "implementation": "",
                "constructor_args": "", "license": "None"}
    r = data["result"][0]
    return {
        "verified":          bool(r.get("SourceCode")),
        "source":            r.get("SourceCode", ""),
        "abi":               r.get("ABI", "[]"),
        "compiler":          r.get("CompilerVersion", ""),
        "proxy":             r.get("Proxy", "0") == "1",
        "implementation":    r.get("Implementation", ""),
        "constructor_args":  r.get("ConstructorArguments", ""),
        "license":           r.get("LicenseType", "None"),
    }


async def get_token_info(address: str, chain_id: int, api_key: str) -> dict:
    data = await _get(
        {"chainid": chain_id, "module": "token",
         "action": "tokeninfo", "contractaddress": address},
        api_key,
    )
    if data.get("status") != "1":
        return {}
    result = data.get("result", [{}])
    return result[0] if isinstance(result, list) and result else {}


async def get_contract_creation(address: str, chain_id: int, api_key: str) -> dict:
    data = await _get(
        {"chainid": chain_id, "module": "contract",
         "action": "getcontractcreation", "contractaddresses": address},
        api_key,
    )
    if data.get("status") != "1":
        return {}
    result = data.get("result", [{}])
    return result[0] if isinstance(result, list) and result else {}


async def get_deployer_address(address: str, chain_id: int, api_key: str) -> str | None:
    try:
        creation = await get_contract_creation(address, chain_id, api_key)
        return creation.get('contractCreator')
    except Exception:
        return None


async def get_bytecode(address: str, chain_id: int, api_key: str) -> str:
    """
    Fetch the runtime bytecode of a deployed contract using Etherscan V2.
    Returns the bytecode as a hex string (with '0x' prefix) or empty string on failure.
    """
    try:
        data = await _get(
            {
                "chainid": chain_id,
                "module": "proxy",
                "action": "eth_getCode",
                "address": address,
                "tag": "latest",
            },
            api_key,
        )
        # Some Etherscan endpoints return result directly as a string
        result = data.get("result", "")
        return result if result and result != "0x" else ""
    except Exception as e:
        print(f"[DEBUG] get_bytecode exception: {e}")
        return ""



async def get_top_holders(address: str, chain_id: int, api_key: str) -> list:
    data = await _get(
        {"chainid": chain_id, "module": "token",
         "action": "tokenholderlist", "contractaddress": address,
         "page": 1, "offset": 10},
        api_key,
    )
    if data.get("status") != "1":
        return []
    return data.get("result", [])


def parse_source_json(source_str: str) -> dict | None:
    if not source_str:
        return None
    
    cleaned = source_str.strip()
    if cleaned.startswith("{{") and cleaned.endswith("}}"):
        cleaned = cleaned[1:-1]
    
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return None
    
    if not isinstance(data, dict):
        return None
    
    result = {}
    
    if "sources" in data and isinstance(data["sources"], dict):
        for filename, filedata in data["sources"].items():
            if isinstance(filedata, dict):
                content = filedata.get("content", "")
                if content:
                    result[filename] = content
            elif isinstance(filedata, str):
                result[filename] = filedata
    else:
        for filename, filedata in data.items():
            if filename in ["language", "settings"]:
                continue
            if isinstance(filedata, dict):
                content = filedata.get("content", "")
                if content:
                    result[filename] = content
                else:
                    result[filename] = str(filedata)
            elif isinstance(filedata, str):
                result[filename] = filedata
    
    return result if result else None


def flatten_source_dict(source_dict: dict) -> str:
    if not source_dict:
        return ""
    combined = []
    for file_name, content in source_dict.items():
        combined.append(f"// File: {file_name}\n{content}")
    return "\n\n".join(combined)


async def fetch_all(address: str, chain_id: int, api_key: str) -> dict:
    if not api_key:
        return {
            "verified": False, "source": "", "abi": "[]",
            "compiler": "", "proxy": False, "implementation": "",
            "license": "None", "constructor_args": "",
            "token_name": "", "token_symbol": "", "total_supply": "",
            "holders": "", "creator": "", "tx_hash": "", "top_holders": [],
        }

    source_t   = get_source_code(address, chain_id, api_key)
    token_t    = get_token_info(address, chain_id, api_key)
    creation_t = get_contract_creation(address, chain_id, api_key)
    holders_t  = get_top_holders(address, chain_id, api_key)

    source, token, creation, holders = await asyncio.gather(
        source_t, token_t, creation_t, holders_t,
        return_exceptions=True,
    )

    def safe(val, default):
        return default if isinstance(val, Exception) else val

    source   = safe(source, {})
    token    = safe(token, {})
    creation = safe(creation, {})
    holders  = safe(holders, [])

    return {
        **source,
        "token_name":    token.get("tokenName", ""),
        "token_symbol":  token.get("symbol", ""),
        "total_supply":  token.get("totalSupply", ""),
        "holders":       token.get("holdersCount", ""),
        "creator":       creation.get("contractCreator", ""),
        "tx_hash":       creation.get("txHash", ""),
        "top_holders":   holders if isinstance(holders, list) else [],
    }


async def get_bytecode_rpc(address: str, chain: str, config: dict) -> str:
    """
    Fetch runtime bytecode using the chain's RPC endpoint (eth_getCode).
    """
    rpc_url = config.get("rpc", {}).get(chain, "")
    if not rpc_url:
        from analysis.mythril_integration import PUBLIC_RPC
        rpc_url = PUBLIC_RPC.get(chain, "")
    if not rpc_url or not rpc_url.startswith("http"):
        return ""

    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getCode",
        "params": [address, "latest"],
        "id": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(rpc_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            result = data.get("result", "")
            return result if result and result != "0x" else ""
    except Exception as e:
        print(f"[DEBUG] RPC bytecode fetch failed: {e}")
        return ""