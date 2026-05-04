# analysis/consensus.py (excerpt of _apply_ground_truth function)

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
            result["risk_score"] = max(result.get("risk_score") or 0.0, 8.0)
        if sc.get("has_proxy_pattern"):
            result["proxy_pattern"] = True

        # Merge static findings (dedup by title)
        existing_titles = {f["title"] for f in result.get("findings", [])}
        for sf in static.get("static_findings", []):
            if sf["title"] not in existing_titles:
                result.setdefault("findings", []).append(sf)
                existing_titles.add(sf["title"])

        # Blend static risk score (30% static, 70% AI)
        static_score = static.get("static_risk_score", 0.0)
        ai_score = result.get("risk_score")
        if ai_score is None:
            ai_score = 5.0  # neutral fallback
        result["risk_score"] = round(
            ai_score * 0.70 + static_score * 0.30, 1
        )
        result["risk_score"] = min(10.0, max(0.0, result["risk_score"]))

    if goplus:
        # GoPlus honeypot is ground truth — always override
        if goplus.get("gp_is_honeypot") is True:
            result["honeypot"]    = True
            result["risk_score"]  = max(result.get("risk_score") or 0.0, 9.0)
            result["recommendation"] = "AVOID"

        if goplus.get("gp_hidden_owner") is True:
            result["hidden_owner"] = True
            result["risk_score"]   = max(result.get("risk_score") or 0.0, 7.5)

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
    current_score = result.get("risk_score") or 5.0
    if current_score >= 7.0 and result.get("recommendation") == "SAFE":
        result["recommendation"] = "AVOID"
    if current_score <= 2.5 and result.get("recommendation") == "AVOID":
        result["recommendation"] = "CAUTION"

    return result