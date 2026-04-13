# analysis/consensus.py
"""
Layer 4: Dual-model consensus engine.
Research shows single models have ~10% hallucination rate and
inconsistent results. Two models must agree within 2.0 points.
Disagreement triggers a third model as tiebreaker.

Flow:
  1. Run Model A (primary, e.g. deepseek-r1)
  2. Run Model B (secondary, e.g. gemini-flash)
  3. Compare scores
     ├─ Agree (diff < 2.0) → average score, merge findings
     └─ Disagree → run Model C (tiebreaker, e.g. claude-sonnet)
                   → use median of all three scores
"""
import json
import asyncio
from ai.client import OpenRouterClient
from analysis.schema import validate_audit, AuditResult

# Model pairs for consensus
CONSENSUS_PAIRS = [
    ("deepseek/deepseek-r1",                "google/gemini-flash-2.0"),
    ("google/gemini-flash-2.0",             "meta-llama/llama-3.3-70b-instruct:free"),
]
TIEBREAKER_MODEL = "anthropic/claude-sonnet-4-5"
AGREEMENT_THRESHOLD = 2.0   # score diff above this = disagreement


async def _run_single(
    messages: list[dict],
    model: str,
    api_key: str,
    max_tokens: int = 4000,
) -> dict:
    """Run one model and return validated result dict."""
    client = OpenRouterClient(
        api_key=api_key, model=model,
        max_tokens=max_tokens, temperature=0.1,
    )
    raw = await client.complete(messages)
    return raw


async def run_consensus_audit(
    messages: list[dict],
    config: dict,
    static_result: dict | None = None,
    goplus_result: dict | None = None,
) -> tuple[dict, dict]:
    """
    Run dual-model consensus audit.

    Returns: (final_result_dict, metadata_dict)
    metadata includes: models_used, agreement, confidence_boost
    """
    api_key = config["openrouter"]["api_key"]

    # Pick model pair based on config
    # Use primary model + a cheap secondary for consensus
    primary_model   = config["openrouter"]["model"]
    secondary_model = "google/gemini-flash-2.0"
    # Don't run consensus with same model twice
    if primary_model == secondary_model:
        secondary_model = "deepseek/deepseek-r1"

    # Run both models concurrently
    result_a_raw, result_b_raw = await asyncio.gather(
        _run_single(messages, primary_model, api_key),
        _run_single(messages, secondary_model, api_key),
        return_exceptions=True,
    )

    # Handle errors gracefully
    if isinstance(result_a_raw, Exception):
        result_a_raw = {}
    if isinstance(result_b_raw, Exception):
        result_b_raw = {}

    # Validate both
    try:
        result_a = validate_audit(result_a_raw)
        score_a  = result_a.risk_score
    except Exception:
        result_a = None
        score_a  = None

    try:
        result_b = validate_audit(result_b_raw)
        score_b  = result_b.risk_score
    except Exception:
        result_b = None
        score_b  = None

    # If only one worked, use it
    if result_a is None and result_b is not None:
        final = result_b
        meta = {"models_used": [secondary_model], "agreement": None,
                "consensus": False, "confidence": "MEDIUM"}
    elif result_b is None and result_a is not None:
        final = result_a
        meta = {"models_used": [primary_model], "agreement": None,
                "consensus": False, "confidence": "MEDIUM"}
    elif result_a is None and result_b is None:
        # Both failed — return minimal safe result
        return (
            {"risk_score": 5.0, "recommendation": "CAUTION",
             "summary": "Analysis unavailable — API error.",
             "findings": [], "honeypot": False, "mint_function": False,
             "owner_renounced": False, "proxy_pattern": False,
             "hidden_owner": False, "transfer_tax_modifiable": False,
             "blacklist_function": False, "max_tx_limit": False,
             "liquidity_concerns": False, "positive_signals": [],
             "audit_cost_equivalent": "$8,000-$20,000"},
            {"models_used": [], "agreement": None,
             "consensus": False, "confidence": "VERY_LOW"},
        )
    else:
        diff = abs(score_a - score_b)

        if diff <= AGREEMENT_THRESHOLD:
            # Models agree — use average score, merge findings
            avg_score = round((score_a + score_b) / 2, 1)
            merged    = _merge_results(result_a, result_b, avg_score)
            final     = merged
            meta = {
                "models_used":  [primary_model, secondary_model],
                "score_a":      score_a,
                "score_b":      score_b,
                "agreement":    round(diff, 2),
                "consensus":    True,
                "confidence":   "HIGH",
            }
        else:
            # Disagreement — run tiebreaker
            result_c_raw = await _run_single(
                messages, TIEBREAKER_MODEL, api_key, max_tokens=4000
            )
            try:
                result_c = validate_audit(result_c_raw)
                score_c  = result_c.risk_score
            except Exception:
                result_c = result_a   # fallback
                score_c  = score_a

            # Median of three scores
            scores    = sorted([score_a, score_b, score_c])
            med_score = scores[1]
            # Use the result closest to median
            dists     = [abs(score_a - med_score), abs(score_b - med_score),
                         abs(score_c - med_score)]
            closest   = [result_a, result_b, result_c][dists.index(min(dists))]
            final     = closest
            final.risk_score = round(med_score, 1)

            meta = {
                "models_used":  [primary_model, secondary_model, TIEBREAKER_MODEL],
                "score_a":      score_a,
                "score_b":      score_b,
                "score_c":      score_c,
                "agreement":    round(diff, 2),
                "consensus":    False,
                "tiebreaker":   True,
                "confidence":   "MEDIUM",
            }

    # Apply ground truth overrides from static analysis + GoPlus
    final_dict = final.model_dump()
    final_dict = _apply_ground_truth(final_dict, static_result, goplus_result)
    final_dict["_meta"] = meta

    return final_dict, meta


def _merge_results(a: AuditResult, b: AuditResult, avg_score: float) -> AuditResult:
    """Merge two agreeing audit results. Union findings, average score."""
    seen_titles = set()
    merged_findings = []
    for f in (a.findings + b.findings):
        if f.title not in seen_titles:
            seen_titles.add(f.title)
            merged_findings.append(f)

    # Union positive signals
    pos = list(set(a.positive_signals + b.positive_signals))

    # Take the more conservative recommendation
    rec_priority = {"AVOID": 3, "CAUTION": 2, "SAFE": 1}
    rec = a.recommendation if rec_priority.get(a.recommendation, 0) >= \
          rec_priority.get(b.recommendation, 0) else b.recommendation

    # Union boolean flags (OR — if either model found it, flag it)
    return AuditResult(
        risk_score               = avg_score,
        recommendation           = rec,
        honeypot                 = a.honeypot or b.honeypot,
        mint_function            = a.mint_function or b.mint_function,
        owner_renounced          = a.owner_renounced and b.owner_renounced,
        proxy_pattern            = a.proxy_pattern or b.proxy_pattern,
        hidden_owner             = a.hidden_owner or b.hidden_owner,
        transfer_tax_modifiable  = a.transfer_tax_modifiable or b.transfer_tax_modifiable,
        blacklist_function       = a.blacklist_function or b.blacklist_function,
        max_tx_limit             = a.max_tx_limit or b.max_tx_limit,
        liquidity_concerns       = a.liquidity_concerns or b.liquidity_concerns,
        findings                 = merged_findings,
        positive_signals         = pos,
        summary                  = a.summary,   # primary model's summary
        audit_cost_equivalent    = "$8,000-$20,000",
    )


def _apply_ground_truth(
    result: dict,
    static: dict | None,
    goplus: dict | None,
) -> dict:
    """
    Override AI flags with deterministic ground truth.
    Static analysis and GoPlus are binary and always correct.
    AI can change boolean flags but ground truth takes precedence.
    """
    if static:
        sc = static.get("static_checks", {})
        # Override AI boolean flags with static truth
        if sc.get("has_mint_function"):
            result["mint_function"] = True
        if sc.get("has_blacklist"):
            result["blacklist_function"] = True
        if sc.get("has_selfdestruct"):
            # Selfdestruct is always critical — force score up
            result["risk_score"] = max(result["risk_score"], 8.0)
        if sc.get("has_proxy_pattern"):
            result["proxy_pattern"] = True

        # Merge static findings (dedup by title)
        existing_titles = {f["title"] for f in result.get("findings", [])}
        for sf in static.get("static_findings", []):
            if sf["title"] not in existing_titles:
                result.setdefault("findings", []).append(sf)
                existing_titles.add(sf["title"])

        # Blend static risk score (30% static, 70% AI)
        static_score = static.get("static_risk_score", 0)
        result["risk_score"] = round(
            result["risk_score"] * 0.70 + static_score * 0.30, 1
        )
        result["risk_score"] = min(10.0, max(0.0, result["risk_score"]))

    if goplus:
        # GoPlus honeypot is ground truth — always override
        if goplus.get("gp_is_honeypot") is True:
            result["honeypot"]    = True
            result["risk_score"]  = max(result["risk_score"], 9.0)
            result["recommendation"] = "AVOID"

        if goplus.get("gp_hidden_owner") is True:
            result["hidden_owner"] = True
            result["risk_score"]   = max(result["risk_score"], 7.5)

        if goplus.get("gp_is_mintable") is True:
            result["mint_function"] = True

        if goplus.get("gp_transfer_pausable") is True:
            result["blacklist_function"] = True

        # Merge GoPlus signals
        from analysis.goplus_check import goplus_risk_signals
        gp_signals = goplus_risk_signals(goplus)
        existing_titles = {f.get("title","") for f in result.get("findings", [])}
        for sig in gp_signals:
            if sig["title"] not in existing_titles:
                result.setdefault("findings", []).append(sig)

        result["goplus_data"] = {
            k: v for k, v in goplus.items()
            if k.startswith("gp_")
        }

    # Final consistency check
    if result["risk_score"] >= 7.0 and result["recommendation"] == "SAFE":
        result["recommendation"] = "AVOID"
    if result["risk_score"] <= 2.5 and result["recommendation"] == "AVOID":
        result["recommendation"] = "CAUTION"

    return result
