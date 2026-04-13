"""
Etherscan V2 API fetcher.
Single API key → all EVM chains via ?chainid=<id>
Base URL: https://api.etherscan.io/v2/api
"""
import json
import asyncio
import httpx
from utils.rate_limiter import etherscan_limiter

BASE = "https://api.etherscan.io/v2/api"


async def _get(params: dict, api_key: str) -> dict:
    """Throttled GET wrapper."""
    await etherscan_limiter.acquire()
    params["apikey"] = api_key
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(BASE, params=params)
        r.raise_for_status()
        return r.json()


async def is_contract(address: str, chain_id: int, api_key: str) -> bool:
    """
    Determine if an address is a smart contract.
    Uses multiple methods for reliability.
    Returns True if contract, False otherwise.
    """
    # Method 1: Check bytecode
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

    # Method 2: Check if source code is verified (strong indicator)
    try:
        src = await get_source_code(address, chain_id, api_key)
        if src.get("verified"):
            return True
    except Exception:
        pass

    # Method 3: If the address has a contract creation transaction
    try:
        creation = await get_contract_creation(address, chain_id, api_key)
        if creation.get("contractCreator"):
            return True
    except Exception:
        pass

    # If all methods fail, assume it's a contract (safer for audits)
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
    """
    Parse Etherscan/PolygonScan's multi‑file JSON source into a dict of {filename: content}.
    Handles:
    - Raw Solidity string
    - Simple {"file.sol": "content"} format
    - Standard JSON input format with {"sources": {"file.sol": {"content": "..."}}}
    - PolygonScan's {{...}} wrapped format
    """
    if not source_str:
        return None
    
    # Remove surrounding {{ }} if present (PolygonScan format)
    cleaned = source_str.strip()
    if cleaned.startswith("{{") and cleaned.endswith("}}"):
        cleaned = cleaned[1:-1]
    
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        # Not JSON, assume raw source
        return None
    
    if not isinstance(data, dict):
        return None
    
    result = {}
    
    # Check if this is Standard JSON input format (has "sources" key)
    if "sources" in data and isinstance(data["sources"], dict):
        # Extract files from "sources" object
        for filename, filedata in data["sources"].items():
            if isinstance(filedata, dict):
                content = filedata.get("content", "")
                if content:
                    result[filename] = content
            elif isinstance(filedata, str):
                result[filename] = filedata
    else:
        # Simple format: keys are filenames
        for filename, filedata in data.items():
            # Skip Standard JSON metadata keys
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
    """Convert a dict of files into a single flattened Solidity string (for AI)."""
    if not source_dict:
        return ""
    combined = []
    for file_name, content in source_dict.items():
        combined.append(f"// File: {file_name}\n{content}")
    return "\n\n".join(combined)


async def fetch_all(address: str, chain_id: int, api_key: str) -> dict:
    """Fetch all contract data concurrently."""
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
