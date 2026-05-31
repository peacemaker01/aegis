# ai/deployer_prompt.py
"""
AI prompt builder for deployer forensics analysis.
Includes outcome classification (rugged / abandoned / active) so the AI
can produce accurate, evidence-based verdicts rather than guessing.
"""
from datetime import datetime, timezone

DEPLOYER_SYSTEM_PROMPT = """\
You are a forensic blockchain analyst. Your job is to interpret on-chain evidence
and produce accurate risk verdicts. Being overly generous gets people rugged.

VERDICT SCALE (choose the most accurate):
- "SERIAL RUGGER"      → 3+ confirmed rugged tokens. Score: 0–15.
- "HIGH RUG RISK"      → 1–2 confirmed rugs OR >70% rug rate. Score: 10–30.
- "SUSPICIOUS PATTERN" → rapid bursts, unverified code, low holder history but no confirmed rugs. Score: 30–55.
- "RUG HISTORY: UNKNOWN" → fresh wallet, no confirmed rugs, limited data. Score: 50–70.
- "CLEAN EOA"          → no deployments, clean user wallet. Score: 80–100.

HARD RULES:
1. If rugged_count >= 3 → verdict MUST be "SERIAL RUGGER", score <= 15.
2. If rugged_count >= 1 → verdict MUST be "HIGH RUG RISK" or worse, score <= 35.
3. If rug_rate >= 0.7 AND rugged_count >= 1 → score <= 20.
4. Never output verdict "LOW_RISK" or "TRUST" — those do not exist.
5. Score is a RISK indicator: 0 = extreme risk, 100 = clean/unknown.
6. avg_time_to_rug_hours gives you the typical window before funds are pulled.
   Use it in the summary to warn users ("this deployer typically rugs within Xh").

REQUIRED OUTPUT (strict JSON, no extra keys):
{
  "reputation_score": <int 0-100>,
  "verdict": <string>,
  "recommendation": <string>,
  "summary": <string, 2-3 sentences, include rug count and time-to-rug if available>,
  "red_flags": [<string>],
  "findings": [{"severity": <"CRITICAL"|"HIGH"|"MEDIUM"|"LOW">, "title": <string>, "description": <string>}]
}
"""


def _format_deployments(deployments: list[dict], limit: int = 12) -> str:
    lines = []
    outcome_icons = {"RUGGED": "⛔", "ABANDONED": "⚠️", "ACTIVE": "✅", "UNKNOWN": "❓"}
    for d in deployments[:limit]:
        name    = d.get("token_name", "") or d.get("contract_name", "") or "Unknown"
        symbol  = d.get("token_symbol", "")
        holders = d.get("holder_count", "?")
        verified = "✓ verified" if d.get("verified") else "✗ unverified"
        outcome  = d.get("outcome", "UNKNOWN")
        icon     = outcome_icons.get(outcome, "❓")
        lines.append(
            f"  {icon} [{d['chain'].upper()}] {d['date']}  "
            f"{name} ({symbol})  "
            f"holders={holders}  {verified}  outcome={outcome}\n"
            f"  address={d['contract_address']}"
        )
    if len(deployments) > limit:
        lines.append(f"  ... and {len(deployments) - limit} more contracts")
    return "\n".join(lines)


def build_deployer_prompt(profile: dict) -> list[dict]:
    funder       = profile.get("funder", {})
    risk_profile = profile.get("risk_profile", {})
    nansen       = profile.get("nansen", {})
    nansen_label = nansen.get("label", {})
    nansen_rep   = nansen.get("reputation", {})

    nansen_str = "NANSEN INSTITUTIONAL REPUTATION: Not Available"
    if nansen_label or nansen_rep:
        parts = []
        if nansen_label:
            parts.append(f"  Wallet Label:    {nansen_label.get('label', 'none')}")
            parts.append(f"  Entity Type:     {nansen_label.get('entity', 'unknown')}")
            parts.append(f"  Is Smart Money:  {nansen_label.get('is_smart_money', False)}")
        if nansen_rep:
            parts.append(f"  Nansen Rep Score:   {nansen_rep.get('reputation_score', 5.0)}/10.0")
            parts.append(f"  Is Known Scammer:   {nansen_rep.get('is_known_scammer', False)}")
            parts.append(f"  Scam Confidence:    {float(nansen_rep.get('scam_confidence') or 0.0):.1%}")
            parts.append(f"  Failed Contracts:   {nansen_rep.get('failed_contracts', 0)}")
            parts.append(f"  Platforms Used:     {', '.join(nansen_rep.get('platforms_used', [])) or 'none'}")
        nansen_str = "NANSEN INSTITUTIONAL REPUTATION:\n" + "\n".join(parts)

    # Outcome summary line
    rugged    = risk_profile.get("rugged_count", 0)
    abandoned = risk_profile.get("abandoned_count", 0)
    active    = risk_profile.get("active_count", 0)
    rug_rate  = risk_profile.get("rug_rate", 0.0)
    avg_rug   = risk_profile.get("avg_time_to_rug_hours")
    total_dep = profile.get("total_deployments", 0)

    outcome_summary = (
        f"  ⛔ Rugged:    {rugged}/{total_dep}  ({rug_rate:.0%} rug rate)\n"
        f"  ⚠️  Abandoned: {abandoned}/{total_dep}\n"
        f"  ✅ Active:    {active}/{total_dep}\n"
    )
    if avg_rug is not None:
        outcome_summary += f"  ⏱️ Avg time-to-rug: ~{avg_rug:.0f} hours\n"

    user_msg = f"""
DEPLOYER WALLET: {profile['deployer']}
TOTAL CONTRACTS DEPLOYED: {profile['total_deployments']}
CHAINS ACTIVE: {', '.join(profile['chains_active']) or 'none found'}
CHAINS SCANNED: {', '.join(profile['chains_scanned'])}

FUNDING SOURCE:
  Funder wallet: {funder.get('funder_address', 'unknown')}
  Funding date:  {funder.get('funding_date', 'unknown')}
  Amount funded: {float(funder.get('funding_value_eth') or 0.0):.4f} ETH/SOL
  Funding tx:    {funder.get('funding_tx', 'unknown')}

OUTCOME ANALYSIS (from on-chain holder data):
{outcome_summary}
RISK SIGNALS (Pre-computed):
  Reputation Score:     {risk_profile.get('reputation_score', 100)}/100
  Multi-chain deployer: {risk_profile.get('multi_chain', False)}
  Unverified ratio:     {float(risk_profile.get('unverified_ratio') or 0.0):.1%}
  Rapid burst detected: {risk_profile.get('rapid_burst', False)}
  Flags: {', '.join(risk_profile.get('risk_flags', [])) or 'none'}

{nansen_str}

DEPLOYMENT HISTORY (newest first, with outcomes):
{_format_deployments(profile.get('deployments', []))}
"""
    return [
        {"role": "system", "content": DEPLOYER_SYSTEM_PROMPT},
        {"role": "user",   "content": user_msg},
    ]