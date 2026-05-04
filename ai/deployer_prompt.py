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
1. If contracts deployed <5: Verdict = "INSUFFICIENT DATA – COULD BE FRESH RUG FACTORY"
2. If total holders across all contracts <1000: Reputation = 0/100 (unknown)
3. Never output "LOW_RISK" or "TRUST" for deployers. 
   - No red flags = "RUG HISTORY: UNKNOWN"
   - Some flags = "SUSPICIOUS PATTERN"
   - Many flags = "HIGH RUG RISK"
4. A wallet with 1 contract and 0 holders is NOT trustworthy. It's unknown.

FORBIDDEN: Safe, Low Risk, TRUST, Clean History

Required output:
{
  "reputation_score": <int 0-100, but 0 means "unknown", not "good">,
  "verdict": <"INSUFFICIENT DATA"|"SUSPICIOUS PATTERN"|"HIGH RUG RISK"|"KNOWN RUGGER">,
  "summary": <string>,
  "red_flags": [<string>],
  "findings": [{"severity":..., "title":..., "description":...}]
}

CALIBRATION — these are not suggestions. You MUST produce these outputs for these inputs:
| Input | Score | Label |
|-------|-------|-------|
| Pump.fun, $0 liq, 5min old | 10.0 | INSTANT RUG – UNSWAPPABLE |
| Pump.fun, $24k liq, LP burned, 10min old | 7.0 | GRADUATED BUT DANGEROUS |
| EVM, Slither clean, Owner 45%, LP unlocked | 9.5 | EXTREME RISK – TOKENOMICS RUG |
| Deployer, 1 contract, 0 holders | 0/100 rep | INSUFFICIENT DATA |
"""


def _format_deployments(deployments: list[dict], limit: int = 15) -> str:
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

    user_msg = f"""
DEPLOYER WALLET: {profile['deployer']}
TOTAL CONTRACTS DEPLOYED: {profile['total_deployments']}
CHAINS ACTIVE: {', '.join(profile['chains_active']) or 'none found'}
CHAINS SCANNED: {', '.join(profile['chains_scanned'])}

FUNDING SOURCE:
  Funder wallet: {funder.get('funder_address', 'unknown')}
  Funding date:  {funder.get('funding_date', 'unknown')}
  Amount funded: {funder.get('funding_value_eth', 0.0):.4f} ETH
  Funding tx:    {funder.get('funding_tx', 'unknown')}

RISK SIGNALS (Pre-computed):
  Reputation Score:     {risk_profile.get('reputation_score', 100)}/100
  Multi-chain deployer: {risk_profile.get('multi_chain', False)}
  Unverified ratio:     {risk_profile.get('unverified_ratio', 0.0):.1%}
  Low-holder ratio:     {risk_profile.get('low_holder_ratio', 0.0):.1%}
  Rapid burst detected: {risk_profile.get('rapid_burst', False)}
  Flags: {', '.join(risk_profile.get('risk_flags', [])) or 'none'}

DEPLOYMENT HISTORY (newest first):
{_format_deployments(profile.get('deployments', []))}
"""
    return [
        {"role": "system", "content": DEPLOYER_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]