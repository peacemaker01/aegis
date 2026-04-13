# analysis/retry.py
"""
Retry + validation wrapper for all AI calls.
Handles: malformed JSON, schema errors, empty responses, API timeouts.
Max 3 attempts with exponential backoff.
"""
import json
import asyncio
from pydantic import ValidationError
from ai.client import OpenRouterClient
from analysis.schema import validate_audit, validate_deployer, validate_monitor


MAX_RETRIES   = 3
BASE_DELAY    = 2.0    # seconds
BACKOFF_MULT  = 2.0


class AIResponseError(Exception):
    """Raised when all retries are exhausted."""
    pass


async def call_with_retry(
    messages: list[dict],
    config: dict,
    validator_fn,
    model_override: str | None = None,
    max_tokens: int = 4000,
) -> dict:
    """
    Call OpenRouter and validate response. Auto-retry on failure.
    validator_fn: one of validate_audit, validate_deployer, validate_monitor
    """
    model  = model_override or config["openrouter"]["model"]
    api_key = config["openrouter"]["api_key"]
    client = OpenRouterClient(
        api_key=api_key, model=model,
        max_tokens=max_tokens, temperature=0.1,
    )

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = await client.complete(messages)

            if not raw:
                raise ValueError("Empty response from AI")

            # Validate schema — raises ValidationError if wrong
            validated = validator_fn(raw)
            return validated.model_dump()

        except (json.JSONDecodeError, KeyError) as e:
            last_error = f"JSON parse error: {e}"
        except ValidationError as e:
            last_error = f"Schema validation failed: {e.error_count()} errors"
            # Add correction instruction to messages on retry
            if attempt < MAX_RETRIES:
                messages = messages + [{
                    "role": "assistant",
                    "content": str(raw) if "raw" in dir() else "{}",
                }, {
                    "role": "user",
                    "content": (
                        f"Your previous response had schema errors: {last_error}. "
                        "Please try again and return ONLY valid JSON matching "
                        "the exact schema specified in the system prompt."
                    ),
                }]
        except Exception as e:
            last_error = f"API error: {type(e).__name__}: {e}"

        if attempt < MAX_RETRIES:
            delay = BASE_DELAY * (BACKOFF_MULT ** (attempt - 1))
            await asyncio.sleep(delay)

    raise AIResponseError(
        f"All {MAX_RETRIES} attempts failed. Last error: {last_error}"
    )


async def safe_audit(messages: list[dict], config: dict, **kwargs) -> dict:
    """Audit with retry + schema validation. Returns fallback on total failure."""
    try:
        return await call_with_retry(messages, config, validate_audit, **kwargs)
    except AIResponseError:
        return _fallback_audit()


async def safe_deployer(messages: list[dict], config: dict, **kwargs) -> dict:
    """Deployer analysis with retry. Returns fallback on total failure."""
    try:
        return await call_with_retry(messages, config, validate_deployer, **kwargs)
    except AIResponseError:
        return _fallback_deployer()


async def safe_monitor(messages: list[dict], config: dict, **kwargs) -> dict:
    try:
        return await call_with_retry(messages, config, validate_monitor,
                                     max_tokens=600, **kwargs)
    except AIResponseError:
        return {"alert": False, "alert_level": "NONE",
                "message": "Analysis failed", "changes": [],
                "new_risk_score": 0.0, "old_risk_score": 0.0, "action": "NONE"}


def _fallback_audit() -> dict:
    """Safe fallback when AI is completely unavailable."""
    return {
        "risk_score": 5.0,
        "recommendation": "CAUTION",
        "honeypot": False, "mint_function": False, "owner_renounced": False,
        "proxy_pattern": False, "hidden_owner": False,
        "transfer_tax_modifiable": False, "blacklist_function": False,
        "max_tx_limit": False, "liquidity_concerns": False,
        "findings": [{
            "severity": "INFO",
            "title": "Analysis Unavailable",
            "description": "AI analysis could not be completed. "
                           "Check your OpenRouter API key and try again.",
        }],
        "positive_signals": [],
        "summary": "AI analysis failed. Static analysis results shown only. "
                   "Manual review recommended before investing.",
        "audit_cost_equivalent": "$8,000-$20,000",
        "_fallback": True,
    }


def _fallback_deployer() -> dict:
    return {
        "risk_score": 5.0, "verdict": "SUSPICIOUS",
        "recommendation": "CAUTION", "pattern": "Analysis unavailable",
        "findings": [], "red_flags": [],
        "chain_hopping": False, "identity_obfuscation": False,
        "reuse_pattern": False, "estimated_victims": None,
        "total_contracts_deployed": 0,
        "summary": "Deployer analysis failed. Manual review recommended.",
        "_fallback": True,
    }
