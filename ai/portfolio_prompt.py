# ai/portfolio_prompt.py
"""AI prompts for portfolio risk analysis and wallet tracking."""

PORTFOLIO_SYSTEM_PROMPT = """\
You are a DeFi portfolio risk analyst. You receive a list of token holdings with
their individual audit results and must assess the OVERALL portfolio risk.

Return ONLY valid JSON — no markdown, no text outside JSON.

Required schema:
{
  "portfolio_risk_score": <float 0.0-10.0>,
  "risk_grade": <"A"|"B"|"C"|"D"|"F">,
  "critical_holdings": [<contract_address string>],
  "safe_holdings":     [<contract_address string>],
  "findings": [
    {
      "severity": <"CRITICAL"|"HIGH"|"MEDIUM"|"LOW"|"INFO">,
      "title":    <string>,
      "description": <string>
    }
  ],
  "pct_high_risk":   <float 0-100>,
  "pct_safe":        <float 0-100>,
  "concentration_risk": <bool>,
  "recommendations": [<string>],
  "summary": <2-3 sentence plain English summary>
}

Grade scale:
  A : 0-2   — healthy, low-risk portfolio
  B : 2-4   — mostly safe, minor concerns
  C : 4-6   — mixed, some risky assets
  D : 6-8   — significant risk exposure
  F : 8-10  — majority high-risk or critical
"""

MONITOR_SYSTEM_PROMPT = """\
You are a smart contract security monitor. You receive a new audit result
for a contract that was previously audited, and must determine if anything
has changed significantly enough to alert the holder.

Return ONLY valid JSON:
{
  "alert": <bool>,
  "alert_level": <"NONE"|"INFO"|"WARNING"|"CRITICAL">,
  "changes": [<string describing what changed>],
  "new_risk_score": <float>,
  "old_risk_score": <float>,
  "message": <one-line alert message for the user>,
  "action": <"NONE"|"REVIEW"|"SELL_IMMEDIATELY">
}
"""


def build_portfolio_prompt(
    wallet: str,
    chain: str,
    holdings_with_audits: list[dict],
) -> list[dict]:
    """Build portfolio analysis prompt from holdings + their audit results."""

    holding_lines = []
    for i, h in enumerate(holdings_with_audits, 1):
        audit = h.get("audit", {})
        score = audit.get("risk_score", "N/A")
        rec   = audit.get("recommendation", "UNKNOWN")
        name  = h.get("token_name") or h.get("token_symbol") or "Unknown"
        addr  = h.get("contract_address", "")
        bal   = h.get("balance", 0)
        holding_lines.append(
            f"  {i}. {name} ({h.get('token_symbol','')})\n"
            f"     address={addr}\n"
            f"     balance={bal}\n"
            f"     risk_score={score}  recommendation={rec}\n"
            f"     honeypot={audit.get('honeypot','?')}  "
            f"mint_fn={audit.get('mint_function','?')}  "
            f"owner_renounced={audit.get('owner_renounced','?')}"
        )

    user_msg = f"""
WALLET: {wallet}
CHAIN:  {chain.upper()}
TOTAL TOKENS HELD: {len(holdings_with_audits)}

HOLDINGS WITH RISK AUDITS:
{"".join(holding_lines) or "  No holdings found."}
"""
    return [
        {"role": "system", "content": PORTFOLIO_SYSTEM_PROMPT},
        {"role": "user",   "content": user_msg},
    ]


def build_monitor_prompt(
    address: str,
    chain: str,
    label: str,
    old_result: dict,
    new_result: dict,
) -> list[dict]:
    """Build a change-detection prompt for the monitor."""
    user_msg = f"""
CONTRACT: {address}
CHAIN:    {chain.upper()}
LABEL:    {label}

PREVIOUS AUDIT:
  risk_score:    {old_result.get('risk_score', 'N/A')}
  recommendation:{old_result.get('recommendation', 'N/A')}
  honeypot:      {old_result.get('honeypot', 'N/A')}
  mint_function: {old_result.get('mint_function', 'N/A')}
  owner_renounced:{old_result.get('owner_renounced', 'N/A')}
  blacklist:     {old_result.get('blacklist_function', 'N/A')}

CURRENT AUDIT:
  risk_score:    {new_result.get('risk_score', 'N/A')}
  recommendation:{new_result.get('recommendation', 'N/A')}
  honeypot:      {new_result.get('honeypot', 'N/A')}
  mint_function: {new_result.get('mint_function', 'N/A')}
  owner_renounced:{new_result.get('owner_renounced', 'N/A')}
  blacklist:     {new_result.get('blacklist_function', 'N/A')}

Determine if there is a meaningful change that warrants alerting the user.
"""
    return [
        {"role": "system", "content": MONITOR_SYSTEM_PROMPT},
        {"role": "user",   "content": user_msg},
    ]
