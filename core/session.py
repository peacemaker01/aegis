# core/session.py
import asyncio
import json
from typing import AsyncGenerator

from core.chains import get_chain
from fetchers.etherscan import fetch_all, get_deployer_address, is_contract, parse_source_json, flatten_source_dict
from ai.client import OpenRouterClient
from ai.prompt_builder import build_audit_prompt, build_qa_prompt
from utils.cache import get_cached, set_cached
from utils.validators import is_valid_address

# Slither integration (Python API, multi‑file support)
from analysis.slither_integration import run_slither_on_source

# Optional advanced analysis modules
try:
    from analysis.static_checks import run_static_checks, static_to_dict
    from analysis.goplus_check import fetch_goplus, parse_goplus
    from analysis.consensus import run_consensus_audit
    from analysis.retry import safe_audit
    ADVANCED_ANALYSIS = True
except ImportError:
    ADVANCED_ANALYSIS = False


async def run_audit(
    address: str,
    chain_name: str,
    config: dict,
    stream: bool = True,
    use_consensus: bool = False,
    debug: bool = False,
) -> tuple[dict, dict | AsyncGenerator]:
    """
    Full accuracy pipeline:
    1. Validate address and check it's a contract
    2. Fetch contract data (source, token info, creation)
    3. Run Slither on the source code (deterministic)
    4. (Optional) static checks, GoPlus, consensus AI
    5. Return contract dict and AI result (or generator)
    """
    if not is_valid_address(address):
        raise ValueError(f"Invalid address: {address}")

    chain = get_chain(chain_name)
    api_key = config["explorers"].get("etherscan", "")

    # ── Check if address is a contract (using improved is_contract) ──
    if api_key:
        if not await is_contract(address, chain["id"], api_key):
            raise ValueError(
                f"Address {address} is not a smart contract (it's an externally owned account). "
                "Use 'aegis wallet' to analyze token holdings of a wallet, or provide a contract address."
            )

    # ── Fetch contract data (cached if possible) ──────────────────────
    cached = get_cached(address, chain["key"])
    if cached and cached.get("verified"):
        contract = cached
    else:
        contract = await fetch_all(address, chain["id"], api_key)
        contract["address"] = address
        contract["chain"] = chain["key"]
        contract["chain_id"] = chain["id"]
        set_cached(address, chain["key"], contract)

    # ── Normalise source code (Etherscan may return JSON) ──────────────
    raw_source = contract.get("source", "")
    source_dict = parse_source_json(raw_source)
    if source_dict:
        source_flattened = flatten_source_dict(source_dict)   # for AI prompt
    else:
        source_flattened = raw_source
        source_dict = None

    # ── Run Slither on multi‑file source (if available) ────────────────
    slither_findings = []
    if source_dict:
        slither_findings = run_slither_on_source(source_dict, debug=debug)
    elif raw_source:
        slither_findings = run_slither_on_source(raw_source, debug=debug)
    contract["slither_findings"] = slither_findings   # store for later use

    # Use flattened source for static checks and AI prompt
    source = source_flattened

    # ── Advanced analysis (static, GoPlus, consensus) or simple AI ────
    if ADVANCED_ANALYSIS and not stream:
        # Static regex checks
        static_result = run_static_checks(source)
        static_dict = static_to_dict(static_result)

        # GoPlus ground truth
        goplus_raw = await fetch_goplus(address, chain["key"])
        goplus_parsed = parse_goplus(goplus_raw)

        contract["static_checks"] = static_dict
        contract["goplus"] = goplus_parsed
        messages = build_audit_prompt(contract)

        if use_consensus:
            result, meta = await run_consensus_audit(
                messages, config, static_dict, goplus_parsed
            )
            result["slither_findings"] = slither_findings
            return contract, result
        else:
            result = await safe_audit(messages, config)
            from analysis.consensus import _apply_ground_truth
            result = _apply_ground_truth(result, static_dict, goplus_parsed)
            result["slither_findings"] = slither_findings
            return contract, result
    else:
        # Simple AI audit (streaming or non‑streaming)
        messages = build_audit_prompt(contract)
        client = OpenRouterClient(
            api_key=config["openrouter"]["api_key"],
            model=config["openrouter"]["model"],
            max_tokens=config["openrouter"]["max_tokens"],
            temperature=config["openrouter"]["temperature"],
            json_mode=True,
        )
        if stream:
            # For streaming, Slither findings are attached to contract
            # and will be merged in cli/audit.py after JSON parsing
            return contract, client.stream_audit(messages)
        else:
            result = await client.complete(messages)
            result["slither_findings"] = slither_findings
            return contract, result


async def run_qa(
    address: str,
    chain_name: str,
    config: dict,
    history: list[dict],
    question: str,
    audit_result: dict = None,
) -> AsyncGenerator:
    """
    Follow‑up Q&A – ensures deployer address is present and uses natural language output.
    The contract dictionary (from cache) includes slither_findings.
    The audit_result also includes slither_findings as a fallback.
    """
    chain = get_chain(chain_name)
    contract = get_cached(address, chain["key"]) or {}
    contract.setdefault("address", address)
    contract.setdefault("chain", chain["key"])
    contract.setdefault("chain_id", chain["id"])

    # Ensure deployer address is present (fetch from Etherscan if missing)
    api_key = config["explorers"].get("etherscan", "")
    if api_key and not contract.get("creator"):
        try:
            deployer = await get_deployer_address(address, chain["id"], api_key)
            if deployer:
                contract["creator"] = deployer
                set_cached(address, chain["key"], contract)
        except Exception:
            pass

    # Build Q&A prompt – it will extract slither_findings from contract (or audit_result)
    messages = build_qa_prompt(contract, history, question, audit_result=audit_result)

    # Create client with JSON mode OFF – Q&A should return natural language
    client = OpenRouterClient(
        api_key=config["openrouter"]["api_key"],
        model=config["openrouter"]["model"],
        max_tokens=1000,
        temperature=0.3,
        json_mode=False,
    )
    return client.stream_audit(messages)
