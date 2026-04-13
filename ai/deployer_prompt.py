# ai/deployer_prompt.py
"""
AI prompt builder for deployer forensics analysis.
Takes the raw deployer profile and asks the AI to reason about
patterns, history, and serial rug behaviour.
"""
from datetime import datetime, timezone

DEPLOYER_SYSTEM_PROMPT = """\
You are a blockchain forensics expert specialising in identifying serial rug pull operators.
Analyse the deployer wallet history provided and return ONLY valid JSON — no text outside JSON.

Required schema:
{
  "risk_score": <float 0.0-10.0>,
  "verdict": <"CLEAN"|"SUSPICIOUS"|"KNOWN_RUGGER"|"SERIAL_RUGGER">,
  "pattern": <one-line description of the deployer's behaviour pattern>,
  "findings": [
    {
      "severity": <"CRITICAL"|"HIGH"|"MEDIUM"|"LOW"|"INFO">,
      "title": <string>,
      "description": <string>
    }
  ],
  "chain_hopping": <bool>,
  "identity_obfuscation": <bool>,
  "reuse_pattern": <bool>,
  "estimated_victims": <int or null>,
  "total_contracts_deployed": <int>,
  "red_flags": [<string>],
  "summary": <2-3 sentence plain English summary>,
  "recommendation": <"TRUST"|"CAUTION"|"AVOID"|"BLACKLIST">
}

Scoring:
  0-2 : Clean — normal developer activity
  3-4 : Suspicious — some concerning patterns
  5-6 : Likely bad actor — multiple red flags
  7-8 : Known rug pattern — strong evidence
  9-10: Serial rugger — definitive serial bad actor

Look for:
  - Repeated deploy + abandon pattern (contract dies within days/weeks)
  - Low holder counts across deployed contracts (pump-and-dump signal)
  - Unverified contracts (hiding malicious code)
  - Multi-chain hopping (evading chain-specific blacklists)
  - Similar token names/symbols (recycling the same scam)
  - Rapid deployment bursts (assembly-line rugger)
  - Known mixer/tornado as funder (identity hiding)
"""


def _format_deployments(deployments: list[dict], limit: int = 15) -> str:
    lines = []
    for d in deployments[:limit]:
        name    = d.get("token_name", "") or d.get("contract_name", "") or "Unknown"
        symbol  = d.get("token_symbol", "")
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
    funder  = profile.get("funder", {})
    signals = profile.get("risk_signals", {})

    user_msg = f"""
DEPLOYER WALLET: {profile['deployer']}
TOTAL CONTRACTS DEPLOYED: {profile['total_deployments']}
CHAINS ACTIVE: {', '.join(profile['chains_active']) or 'none found'}
CHAINS SCANNED: {', '.join(profile['chains_scanned'])}

FUNDING SOURCE:
  Funder wallet: {funder.get('funder_address', 'unknown')}
  Funding date:  {funder.get('funding_date', 'unknown')}
  Amount funded: {funder.get('funding_value_eth', '?')} ETH
  Funding tx:    {funder.get('funding_tx', 'unknown')}

RISK SIGNALS:
  Multi-chain deployer:         {signals.get('multi_chain_deployer', False)}
  Has unverified contracts:     {signals.get('has_unverified_contracts', False)}
  Unverified contract count:    {signals.get('unverified_count', 0)}
  Rapid deployment burst:       {signals.get('rapid_deployments', False)}
  Low-holder contracts (<50):   {signals.get('low_holder_contracts', 0)}

DEPLOYMENT HISTORY (newest first):
{_format_deployments(profile.get('deployments', []))}
"""
    return [
        {"role": "system", "content": DEPLOYER_SYSTEM_PROMPT},
        {"role": "user",   "content": user_msg},
    ]
