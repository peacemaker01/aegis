# ai/portfolio_prompt.py
"""AI prompts for portfolio risk analysis and monitoring."""

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