# core/session.py
import asyncio
import json
import hashlib
import re
from typing import AsyncGenerator, Optional, Callable, Awaitable

from core.chains import get_chain
from fetchers.etherscan import (
    fetch_all, get_deployer_address, is_contract,
    parse_source_json, flatten_source_dict, get_bytecode_rpc
)
from ai.client import OpenRouterClient
from ai.prompt_builder import build_audit_prompt, build_qa_prompt, build_deepscan_prompt
from utils.cache import get_cached, set_cached
from utils.validators import is_valid_address
from utils.health import record_success, record_failure

from analysis.slither_integration import run_slither_on_source

try:
    from analysis.static_checks import run_static_checks, static_to_dict
    from analysis.goplus_check import fetch_goplus, parse_goplus, goplus_risk_signals
    from analysis.consensus import _apply_ground_truth
    from analysis.correlate import correlate_findings
    from analysis.clone_detector import detect_clone
    ADVANCED_ANALYSIS = True
except ImportError:
    ADVANCED_ANALYSIS = False


def _get_source_hash(source: str) -> str:
    sample = source[:5000] + source[-2000:] if len(source) > 7000 else source
    return hashlib.sha256(sample.encode()).hexdigest()[:16]


async def run_audit(
    address: str,
    chain_name: str,
    config: dict,
    stream: bool = True,
    use_consensus: bool = False,
    debug: bool = False,
) -> tuple[dict, dict | AsyncGenerator]:
    if not is_valid_address(address):
        raise ValueError(f"Invalid address: {address}")

    chain = get_chain(chain_name)
    api_key = config["explorers"].get("etherscan", [])
    if isinstance(api_key, list):
        api_key = api_key[0] if api_key else ""

    if api_key and chain["id"]:
        if not await is_contract(address, chain["id"], api_key):
            raise ValueError(f"Address {address} is not a smart contract.")

    cached = await get_cached(address, chain["key"])
    if cached and cached.get("verified"):
        contract = cached
        if "slither_findings" not in contract:
            raw_source = contract.get("source", "")
            source_dict = parse_source_json(raw_source)
            slither_findings = run_slither_on_source(source_dict or raw_source, debug=debug) if source_dict or raw_source else []
            contract["slither_findings"] = slither_findings
            await set_cached(address, chain["key"], contract)
    else:
        contract = await fetch_all(address, chain["id"], api_key)
        contract["address"] = address
        contract["chain"] = chain["key"]
        contract["chain_id"] = chain["id"]
        await set_cached(address, chain["key"], contract)

    raw_source = contract.get("source", "")
    source_dict = parse_source_json(raw_source)
    source = flatten_source_dict(source_dict) if source_dict else raw_source

    slither_findings = contract.get("slither_findings") or []
    if not slither_findings and (source_dict or raw_source):
        slither_findings = run_slither_on_source(source_dict or raw_source, debug=debug)
        contract["slither_findings"] = slither_findings
        await set_cached(address, chain["key"], contract)

    if ADVANCED_ANALYSIS and not stream:
        static_result = run_static_checks(source)
        static_dict = static_to_dict(static_result)
        goplus_raw = await fetch_goplus(address, chain["key"], debug=debug)
        goplus_parsed = parse_goplus(goplus_raw)
        contract["static_checks"] = static_dict
        contract["goplus"] = goplus_parsed
        messages = build_audit_prompt(contract)
        from analysis.retry import safe_audit
        result = await safe_audit(messages, config)
        result = _apply_ground_truth(result, static_dict, goplus_parsed)
        result["slither_findings"] = slither_findings
        return contract, result
    else:
        messages = build_audit_prompt(contract)
        client = OpenRouterClient(
            api_key=config["openrouter"]["api_key"],
            model=config["openrouter"]["model"],
            max_tokens=config["openrouter"]["max_tokens"],
            temperature=0.1, json_mode=True,
            api_keys=config["openrouter"].get("api_keys"),
            timeout=60,
        )
        if stream:
            return contract, client.stream_audit(messages)
        else:
            result = await client.complete(messages)
            result["slither_findings"] = slither_findings
            return contract, result


async def run_scan(
    address: str,
    chain_name: str,
    config: dict,
    stream: bool = False,
    debug: bool = False,
    fast_mode: bool = True,
    progress_callback: Optional[Callable[[str], Awaitable[None]]] = None,
) -> tuple[dict, dict]:
    if not is_valid_address(address):
        raise ValueError(f"Invalid address: {address}")

    chain = get_chain(chain_name)

    # Route to Solana
    if chain["key"] == "solana":
        from aegis_solana.session import run_solana_scan
        raw_data, ai_result = await run_solana_scan(
            address, config, debug=debug,
            fast_mode=fast_mode, progress_callback=progress_callback
        )
        contract = {
            "address": address, "chain": "solana",
            "token_name": raw_data.get("metadata", {}).get("name", ""),
            "token_symbol": raw_data.get("metadata", {}).get("symbol", ""),
        }
        return contract, ai_result

    api_key = config["explorers"].get("etherscan", [])
    if isinstance(api_key, list):
        api_key = api_key[0] if api_key else ""

    if progress_callback:
        await progress_callback("📡 Fetching contract from Etherscan...")
    try:
        cached = await get_cached(address, chain["key"])
        if cached and cached.get("verified"):
            contract = cached
        else:
            contract = await fetch_all(address, chain["id"], api_key)
            contract["address"] = address
            contract["chain"] = chain["key"]
            contract["chain_id"] = chain["id"]
            await set_cached(address, chain["key"], contract)
        record_success("etherscan")
    except Exception as e:
        record_failure("etherscan", str(e))
        if debug:
            print(f"[DEBUG] Etherscan fetch failed: {e}")
        contract = {"address": address, "chain": chain["key"], "chain_id": chain["id"],
                    "verified": False, "source": "", "token_name": "", "token_symbol": ""}
        if progress_callback:
            await progress_callback("⚠️ Contract data fetch failed, continuing with limited analysis.")

    raw_source = contract.get("source", "")
    source_dict = parse_source_json(raw_source)
    source = flatten_source_dict(source_dict) if source_dict else raw_source

    slither_findings = []
    if source_dict or raw_source:
        if progress_callback:
            await progress_callback("🔬 Running Slither static analysis...")
        try:
            slither_findings = run_slither_on_source(
                source_dict or raw_source, debug=debug, deep_scan=not fast_mode
            )
            record_success("slither")
        except Exception as e:
            record_failure("slither", str(e))
            if debug:
                print(f"[DEBUG] Slither failed: {e}")
            if progress_callback:
                await progress_callback("⚠️ Slither analysis failed, skipping.")

    # Mythril has been removed; always empty
    mythril_findings = []

    goplus_parsed = {}
    goplus_signals = []
    if progress_callback:
        await progress_callback("🛡️ Checking GoPlus security API...")
    try:
        goplus_raw = await fetch_goplus(address, chain["key"], debug=debug)
        goplus_parsed = parse_goplus(goplus_raw)
        goplus_signals = goplus_risk_signals(goplus_parsed)
        record_success("goplus")
    except Exception as e:
        record_failure("goplus", str(e))
        if debug:
            print(f"[DEBUG] GoPlus failed: {e}")
        if progress_callback:
            await progress_callback("⚠️ GoPlus check unavailable.")

    static_dict = {}
    if progress_callback:
        await progress_callback("🔎 Running static regex checks...")
    try:
        static_result = run_static_checks(source)
        static_dict = static_to_dict(static_result)
        record_success("static_checks")
    except Exception as e:
        record_failure("static_checks", str(e))
        if debug:
            print(f"[DEBUG] Static checks failed: {e}")
        static_dict = {"static_findings": [], "static_checks": {}}

    # ---- Deep scan enhancements (clone detection) ----
    clone_result = {}
    if not fast_mode and contract.get("verified"):
        if progress_callback:
            await progress_callback("🧬 Checking bytecode similarity to known rug patterns...")
        try:
            # Fetch runtime bytecode using direct RPC with timeout
            bytecode = await asyncio.wait_for(
                get_bytecode_rpc(address, chain["key"], config),
                timeout=10.0
            )
            if bytecode:
                clone_result = await detect_clone(bytecode, debug=debug)
                if debug:
                    print(f"[DEBUG] Clone detection: {len(clone_result.get('matched_selectors', []))} matched selectors, similarity={clone_result.get('similarity_score', 0):.2f}")
            else:
                if debug:
                    print("[DEBUG] Clone detection skipped: no bytecode")
        except asyncio.TimeoutError:
            if debug:
                print("[DEBUG] Clone detection: bytecode fetch timed out")
        except Exception as e:
            record_failure("clone_detector", str(e))
            if debug:
                print(f"[DEBUG] Clone detection failed: {e}")

    if progress_callback:
        await progress_callback("🧠 Correlating findings with AI...")

    correlation = correlate_findings(
        slither_findings, mythril_findings, goplus_signals,
        static_dict.get("static_findings", [])
    )
    messages = build_deepscan_prompt(
        contract, slither_findings, mythril_findings,
        goplus_parsed, static_dict, clone_result=clone_result
    )

    client = OpenRouterClient(
        api_key=config["openrouter"]["api_key"],
        model=config["openrouter"]["model"],
        max_tokens=2000, temperature=0.0, json_mode=True,
        api_keys=config["openrouter"].get("api_keys"),
        timeout=60,
    )

    try:
        raw_response = await client.complete(messages)
        if isinstance(raw_response, dict):
            ai_result = raw_response
        elif isinstance(raw_response, str):
            try:
                ai_result = json.loads(raw_response)
            except json.JSONDecodeError:
                json_match = re.search(r'```json\s*(.*?)\s*```', raw_response, re.DOTALL)
                if json_match:
                    ai_result = json.loads(json_match.group(1))
                else:
                    start = raw_response.find('{')
                    end = raw_response.rfind('}')
                    if start != -1 and end != -1:
                        ai_result = json.loads(raw_response[start:end+1])
                    else:
                        raise
        else:
            raise ValueError(f"Unexpected AI response type: {type(raw_response)}")
        record_success("openrouter")
    except Exception as e:
        record_failure("openrouter", str(e))
        if debug:
            print(f"[DEBUG] AI call failed: {e}")
        ai_result = {"risk_score": 5.0, "recommendation": "CAUTION",
                     "summary": "AI analysis unavailable.", "consensus_findings": [], "single_tool_findings": []}

    result = {
        "risk_score": ai_result.get("risk_score"),
        "recommendation": ai_result.get("recommendation", "CAUTION"),
        "summary": ai_result.get("summary", ""),
        "consensus_findings": ai_result.get("consensus_findings", []),
        "single_tool_findings": ai_result.get("single_tool_findings", []),
        "_raw": {
            "slither": slither_findings, "mythril": mythril_findings,
            "goplus": goplus_parsed, "static": static_dict, "clone": clone_result
        },
        "correlation_stats": {
            "total_tools": correlation["total_tools"],
            "by_severity": correlation["by_severity"]
        }
    }

    if ADVANCED_ANALYSIS:
        result = _apply_ground_truth(result, static_dict, goplus_parsed)

    source_hash = _get_source_hash(source)
    ai_cache_address = f"scan_{address}_{source_hash}"
    await set_cached(ai_cache_address, chain["key"], result)

    return contract, result


async def run_qa(
    address: str, chain_name: str, config: dict, history: list[dict],
    question: str, audit_result: dict = None,
) -> AsyncGenerator:
    chain = get_chain(chain_name)
    contract = await get_cached(address, chain["key"]) or {}
    contract.setdefault("address", address)
    contract.setdefault("chain", chain["key"])
    contract.setdefault("chain_id", chain["id"])

    api_key = config["explorers"].get("etherscan", [])
    if isinstance(api_key, list):
        api_key = api_key[0] if api_key else ""
    if api_key and not contract.get("creator"):
        try:
            deployer = await get_deployer_address(address, chain["id"], api_key)
            if deployer:
                contract["creator"] = deployer
                await set_cached(address, chain["key"], contract)
        except Exception:
            pass

    messages = build_qa_prompt(contract, history, question, audit_result=audit_result)
    client = OpenRouterClient(api_key=config["openrouter"]["api_key"],
                              model=config["openrouter"]["model"],
                              max_tokens=1000, temperature=0.3, json_mode=False,
                              api_keys=config["openrouter"].get("api_keys"))
    return client.stream_audit(messages)