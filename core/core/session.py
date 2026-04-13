# core/session.py
import asyncio
import json
from typing import AsyncGenerator

from core.chains import get_chain
from fetchers.etherscan import fetch_all
from ai.client import OpenRouterClient
from ai.prompt_builder import build_audit_prompt, build_qa_prompt
from utils.cache import get_cached, set_cached
from utils.validators import is_valid_address


async def run_audit(
    address: str,
    chain_name: str,
    config: dict,
    stream: bool = True,
) -> dict | AsyncGenerator:
    if not is_valid_address(address):
        raise ValueError(f"Invalid address: {address}")

    chain   = get_chain(chain_name)
    api_key = config["explorers"].get("etherscan", "")

    cached = get_cached(address, chain["key"])
    if cached and cached.get("verified"):
        contract = cached
    else:
        contract = await fetch_all(address, chain["id"], api_key)
        contract["address"]  = address
        contract["chain"]    = chain["key"]
        contract["chain_id"] = chain["id"]
        set_cached(address, chain["key"], contract)

    messages = build_audit_prompt(contract)

    client = OpenRouterClient(
        api_key     = config["openrouter"]["api_key"],
        model       = config["openrouter"]["model"],
        max_tokens  = config["openrouter"]["max_tokens"],
        temperature = config["openrouter"]["temperature"],
    )

    if stream:
        return contract, client.stream_audit(messages)
    else:
        result = await client.complete(messages)
        return contract, result


async def run_qa(
    address: str,
    chain_name: str,
    config: dict,
    history: list[dict],
    question: str,
    audit_result: dict = None,          # <-- new parameter
) -> AsyncGenerator:
    chain    = get_chain(chain_name)
    contract = get_cached(address, chain["key"]) or {}
    contract.setdefault("address",  address)
    contract.setdefault("chain",    chain["key"])
    contract.setdefault("chain_id", chain["id"])

    messages = build_qa_prompt(contract, history, question, audit_result=audit_result)
    client   = OpenRouterClient(
        api_key     = config["openrouter"]["api_key"],
        model       = config["openrouter"]["model"],
        max_tokens  = 1000,
        temperature = 0.3,
    )
    return client.stream_audit(messages)
