# ai/deployer_prompt.py
"""
AI prompt builder for deployer forensics analysis.
"""
from datetime import datetime, timezone

DEPLOYER_SYSTEM_PROMPT = """\
You are not a financial advisor. You are a casino bouncer. Your job is to tell people
the house edge before they sit down. Being "nice" by lowering risk scores gets people hurt.

You analyze deployer wallet history. Your job is to detect patterns of rug-pulling, not to vouch for wallets.

HARD RULES:
1. If contracts deployed <5:
   - If there are any negative risk signals or red flags (e.g., unverified code, 0 holders, rapid deployment bursts, high sniper concentration): Verdict = "SUSPICIOUS PATTERN" or "HIGH RUG RISK", Reputation = 0-30/100.
   - If the deployments look completely clean (verified code, organic holder counts, no rapid bursts, normal activity): Verdict = "RUG HISTORY: UNKNOWN", Reputation = 50-70/100 (representing a fresh but currently clean developer).
2. Never output "LOW_RISK" or "TRUST" for deployers. 
   - No red flags = "RUG HISTORY: UNKNOWN"
   - Some flags = "SUSPICIOUS PATTERN"
   - Many flags = "HIGH RUG RISK"
3. A wallet with 1 contract and 0 holders is NOT trustworthy. It's unknown.

REQUIRED OUTPUT:
{
  "reputation_score": <int 0-100, but 0 means "unknown", not "good">,
  "verdict": <"INSUFFICIENT DATA"|"SUSPICIOUS PATTERN"|"HIGH RUG RISK"|"KNOWN RUGGER">,
  "recommendation": <string>,
  "summary": <string>,
  "red_flags": [<string>],
  "findings": [{"severity":<string>,"title":<string>,"description":<string>}]
}

HARD RULES TO FOLLOW:
- If contracts deployed <5 and any red flags → Verdict = "SUSPICIOUS PATTERN", Rep 0-30
- If contracts deployed <5 and completely clean → Verdict = "RUG HISTORY: UNKNOWN", Rep 50-70
- Never output "LOW_RISK" or "TRUST"
"""


def _format_deployments(deployments: list[dict], limit: int = 10) -> str:
    lines = []
    for d in deployments[:limit]:
        name = d.get("token_name", "") or d.get("contract_name", "") or "Unknown"
        symbol = d.get("token_symbol", "")
        holders = d.get("holder_count", "?")
        verified = "✓ verified" if d.get("verified") else "✗ unverified"
        lines.append(
            f"  [{d['chain'].upper()}] {d['date']}  "
            f"{name} ({symbol})  "
            f"holders={holders}  {verified}\n"
            f"  address={d['contract_address']}"
        )
    if len(deployments) > limit:
        lines.append(f"  ... and {len(deployments) - limit} more contracts")
    return "\n".join(lines)


def build_deployer_prompt(profile: dict) -> list[dict]:
    funder = profile.get("funder", {})
    risk_profile = profile.get("risk_profile", {})
    nansen = profile.get("nansen", {})
    nansen_label = nansen.get("label", {})
    nansen_rep = nansen.get("reputation", {})

    nansen_str = "NANSEN INSTITUTIONAL REPUTATION: Not Available"
    if nansen_label or nansen_rep:
        nansen_parts = []
        if nansen_label:
            nansen_parts.append(f"  Wallet Label: {nansen_label.get('label', 'none')}")
            nansen_parts.append(f"  Entity Type:  {nansen_label.get('entity', 'unknown')}")
            nansen_parts.append(f"  Is Smart Money: {nansen_label.get('is_smart_money', False)}")
        if nansen_rep:
            nansen_parts.append(f"  Nansen Reputation Score: {nansen_rep.get('reputation_score', 5.0)}/10.0")
            nansen_parts.append(f"  Is Known Scammer:        {nansen_rep.get('is_known_scammer', False)}")
            nansen_parts.append(f"  Scam Confidence:         {float(nansen_rep.get('scam_confidence') or 0.0):.1%}")
            nansen_parts.append(f"  Total Deployments (Nansen): {nansen_rep.get('total_contracts_deployed', 0)}")
            nansen_parts.append(f"  Failed Contracts:        {nansen_rep.get('failed_contracts', 0)}")
            nansen_parts.append(f"  Platforms Historically Used: {', '.join(nansen_rep.get('platforms_used', [])) or 'none'}")
        nansen_str = "NANSEN INSTITUTIONAL REPUTATION:\n" + "\n".join(nansen_parts)

    user_msg = f"""
DEPLOYER WALLET: {profile['deployer']}
TOTAL CONTRACTS DEPLOYED: {profile['total_deployments']}
CHAINS ACTIVE: {', '.join(profile['chains_active']) or 'none found'}
CHAINS SCANNED: {', '.join(profile['chains_scanned'])}

FUNDING SOURCE:
  Funder wallet: {funder.get('funder_address', 'unknown')}
  Funding date:  {funder.get('funding_date', 'unknown')}
  Amount funded: {float(funder.get('funding_value_eth') or 0.0):.4f} ETH
  Funding tx:    {funder.get('funding_tx', 'unknown')}

RISK SIGNALS (Pre-computed):
  Reputation Score:     {risk_profile.get('reputation_score', 100)}/100
  Multi-chain deployer: {risk_profile.get('multi_chain', False)}
  Unverified ratio:     {float(risk_profile.get('unverified_ratio') or 0.0):.1%}
  Low-holder ratio:     {float(risk_profile.get('low_holder_ratio') or 0.0):.1%}
  Rapid burst detected: {risk_profile.get('rapid_burst', False)}
  Flags: {', '.join(risk_profile.get('risk_flags', [])) or 'none'}

{nansen_str}

DEPLOYMENT HISTORY (newest first):
{_format_deployments(profile.get('deployments', []))}
"""
    return [
        {"role": "system", "content": DEPLOYER_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]