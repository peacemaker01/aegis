"""
Layer 2: Nansen Intelligence integration.
Provides institutional context, deployer reputation, and smart money signals.
Used as CONFIDENCE BOOSTER for contract legitimacy and risk assessment.
"""
from typing import Optional, Dict, Any


def parse_nansen_deployer(raw: dict) -> dict:
    """
    Parse Nansen deployer data into Aegis schema.
    Returns standardized deployer intelligence.
    """
    if not raw or not raw.get("nansen_available"):
        return {"nansen_available": False}

    try:
        label_data = raw.get("label", {})
        reputation = raw.get("reputation", {})

        # Determine deployer entity type
        entity = label_data.get("entity", "unknown")
        label = label_data.get("label", "Unknown Address")
        is_smart_money = label_data.get("is_smart_money", False)

        # Red flags
        red_flags = []
        reputation_score = reputation.get("reputation_score", 5.0)
        scam_confidence = reputation.get("scam_confidence", 0.0)
        success_rate = reputation.get("success_rate", 0.5)

        if reputation.get("is_known_scammer"):
            red_flags.append(f"KNOWN SCAMMER in Nansen database (confidence: {scam_confidence:.1%})")

        if success_rate < 0.5 and reputation.get("total_contracts_deployed", 0) > 5:
            red_flags.append(f"Low success rate: {success_rate:.1%} (deployed {reputation.get('total_contracts_deployed')} contracts)")

        if reputation.get("failed_contracts", 0) > 3:
            red_flags.append(f"{reputation.get('failed_contracts')} failed/rugged contracts from this deployer")

        if label_data.get("risk_score", 0) > 7:
            red_flags.append(f"High risk entity score: {label_data.get('risk_score')}")

        # Green flags
        green_flags = []
        if entity in ["exchange", "fund", "market_maker"]:
            green_flags.append(f"Official {entity.title()}: {label}")
        
        if is_smart_money:
            green_flags.append("Labeled as Smart Money by Nansen")

        if success_rate > 0.8 and reputation.get("total_contracts_deployed", 0) > 5:
            green_flags.append(f"Strong track record: {success_rate:.1%} success rate")

        return {
            "nansen_available": True,
            "deployer_label": label,
            "deployer_entity": entity,
            "is_smart_money": is_smart_money,
            "reputation_score": reputation_score,
            "scam_confidence": scam_confidence,
            "success_rate": success_rate,
            "total_contracts_deployed": reputation.get("total_contracts_deployed", 0),
            "failed_contracts": reputation.get("failed_contracts", 0),
            "platforms_used": reputation.get("platforms_used", []),
            "chains_used": reputation.get("chains_used", []),
            "red_flags": red_flags,
            "green_flags": green_flags,
        }
    except Exception as e:
        return {"nansen_available": False, "error": str(e)}


def parse_nansen_contract(raw: dict) -> dict:
    """
    Parse Nansen contract intelligence into Aegis schema.
    Returns smart money signals and holder composition analysis.
    """
    if not raw:
        return {"nansen_available": False}

    try:
        smart_money_data = raw.get("smart_money", {})
        composition_data = raw.get("composition", {})

        smart_money_count = smart_money_data.get("smart_money_count", 0)
        institutional_quality = composition_data.get("institutional_quality", "low")
        smart_money_pct = composition_data.get("smart_money_pct", 0)
        institutional_pct = (
            composition_data.get("fund_pct", 0) + composition_data.get("market_maker_pct", 0)
        )

        # Institutional signals
        signals = []
        if smart_money_data.get("is_accumulating"):
            signals.append("Institutional wallets ACCUMULATING - positive signal")
        elif smart_money_count > 0 and not smart_money_data.get("is_accumulating"):
            signals.append("Institutional wallets DISTRIBUTING - caution signal")

        if smart_money_count > 50:
            signals.append(f"High labeled wallet presence: {smart_money_count} entities")
        elif smart_money_count > 10:
            signals.append(f"Moderate labeled wallet presence: {smart_money_count} entities")

        if institutional_pct > 25:
            signals.append(f"Strong institutional backing: {institutional_pct:.1f}% held by funds/makers")
        elif institutional_pct > 10:
            signals.append(f"Some institutional support: {institutional_pct:.1f}%")

        if composition_data.get("top_10_concentration_pct", 0) > 80:
            signals.append("WARNING: Heavily concentrated holdings (>80% in top 10)")
        elif composition_data.get("top_10_concentration_pct", 0) > 60:
            signals.append("Note: Moderately concentrated holdings")

        # Risk assessment
        risk_factors = []
        if institutional_quality == "low" and smart_money_count == 0:
            risk_factors.append("No institutional or smart money interest")
        
        if composition_data.get("exchange_pct", 0) > 50:
            risk_factors.append("Heavy exchange concentration - potential manipulation risk")

        top_holders = smart_money_data.get("top_holders", [])
        top_holder_labels = [h.get("label") for h in top_holders if h.get("label")]

        return {
            "nansen_available": True,
            "smart_money_count": smart_money_count,
            "smart_money_pct": smart_money_pct,
            "institutional_pct": institutional_pct,
            "institutional_quality": institutional_quality,
            "holder_quality": institutional_quality,
            "is_accumulating": smart_money_data.get("is_accumulating", False),
            "total_holders": composition_data.get("total_holders", 0),
            "top_10_concentration": composition_data.get("top_10_concentration_pct", 0),
            "exchange_pct": composition_data.get("exchange_pct", 0),
            "retail_pct": composition_data.get("retail_pct", 0),
            "smart_money_signals": signals,
            "risk_factors": risk_factors,
            "top_holder_labels": top_holder_labels[:5],  # Top 5
        }
    except Exception as e:
        return {"nansen_available": False, "error": str(e)}


def apply_nansen_to_score(base_score: float, nansen_data: dict, context: str = "contract") -> float:
    """
    Adjust risk score based on Nansen intelligence.
    context: "contract" or "deployer"
    """
    if not nansen_data.get("nansen_available"):
        return base_score

    adjustment = 0.0

    if context == "contract":
        # Positive signals reduce risk
        smart_money_count = nansen_data.get("smart_money_count", 0)
        institutional_quality = nansen_data.get("institutional_quality", "low")
        is_accumulating = nansen_data.get("is_accumulating", False)

        if smart_money_count > 50:
            adjustment -= 1.5
        elif smart_money_count > 20:
            adjustment -= 1.0
        elif smart_money_count > 5:
            adjustment -= 0.5

        if is_accumulating and smart_money_count > 0:
            adjustment -= 0.5

        if institutional_quality == "high":
            adjustment -= 1.0
        elif institutional_quality == "medium":
            adjustment -= 0.5

        # Negative signals increase risk
        if nansen_data.get("top_10_concentration", 0) > 80:
            adjustment += 1.0

        if nansen_data.get("exchange_pct", 0) > 50:
            adjustment += 0.5

    elif context == "deployer":
        # Deployer reputation adjustment
        reputation_score = nansen_data.get("reputation_score", 5.0)
        is_known_scammer = nansen_data.get("scam_confidence", 0.0) > 0.7
        success_rate = nansen_data.get("success_rate", 0.5)
        failed_contracts = nansen_data.get("failed_contracts", 0)

        if is_known_scammer:
            adjustment += 3.0

        if failed_contracts > 5:
            adjustment += 2.0
        elif failed_contracts > 2:
            adjustment += 1.0

        if reputation_score > 8.0:
            adjustment -= 1.5
        elif reputation_score > 6.0:
            adjustment -= 0.5

        if success_rate < 0.3:
            adjustment += 1.5
        elif success_rate < 0.6:
            adjustment += 0.5

    # Clamp adjustment
    adjustment = max(-3.0, min(3.0, adjustment))
    new_score = base_score + adjustment

    return max(0.0, min(10.0, new_score))


def get_nansen_recommendation(
    nansen_data: dict, context: str = "contract"
) -> Optional[str]:
    """
    Generate recommendation override based on strong Nansen signals.
    """
    if not nansen_data.get("nansen_available"):
        return None

    if context == "contract":
        scam_confidence = 0.0
        if nansen_data.get("smart_money_count", 0) > 100:
            return "SAFE"  # Very strong signal

        top_concentration = nansen_data.get("top_10_concentration", 0)
        if top_concentration > 95:
            return "AVOID"  # Extreme concentration = likely rug

    elif context == "deployer":
        scam_confidence = nansen_data.get("scam_confidence", 0.0)
        if scam_confidence > 0.8:
            return "BLACKLIST"

        is_known_scammer = nansen_data.get("red_flags", [])
        if any("KNOWN SCAMMER" in flag for flag in is_known_scammer):
            return "AVOID"

        reputation_score = nansen_data.get("reputation_score", 5.0)
        if reputation_score > 8.5 and nansen_data.get("deployer_entity") in ["exchange", "fund"]:
            return "TRUST"

    return None
