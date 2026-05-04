# ai/portfolio_prompt.py
"""AI prompts for portfolio risk analysis."""

PORTFOLIO_SYSTEM_PROMPT = """\
You are a DeFi portfolio risk analyst. You receive a list of token holdings with
their individual audit results and must assess the OVERALL portfolio risk.

Return ONLY valid JSON – no markdown, no text outside JSON.

Required schema:
{
  "portfolio_risk_score": <float 0.0-10.0>,
  "risk_grade": <"A"|"B"|"C"|"D"|"F">,
  "critical_holdings": [<token_address string>],
  "safe_holdings": [<token_address string>],
  "findings": [
    {
      "severity": <"CRITICAL"|"HIGH"|"MEDIUM"|"LOW"|"INFO">,
      "title": <string>,
      "description": <string>
    }
  ],
  "pct_high_risk": <float 0-100>,
  "pct_safe": <float 0-100>,
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


def build_portfolio_prompt(
    wallet: str,
    holdings_with_audits: list[dict],
    total_value_usd: float,
) -> list[dict]:
    """Build portfolio analysis prompt from holdings + their audit results."""

    holding_lines = []
    zero_value_count = 0
    for i, h in enumerate(holdings_with_audits, 1):
        value = float(h.get("usd_value", 0))
        if value <= 0:
            zero_value_count += 1
            continue

        audit = h.get("audit", {})
        score = audit.get("risk_score", "N/A")
        rec = audit.get("recommendation", "UNKNOWN")
        name = h.get("token_name") or h.get("name") or "Unknown"
        symbol = h.get("token_symbol") or h.get("symbol") or ""
        pct = (value / total_value_usd * 100) if total_value_usd > 0 else 0

        holding_lines.append(
            f"  {i}. {name} ({symbol})\n"
            f"     address={h.get('token_address', '?')}\n"
            f"     value=${value:,.2f} ({pct:.1f}% of portfolio)\n"
            f"     risk_score={score}  recommendation={rec}\n"
        )

    if zero_value_count > 0:
        holding_lines.append(f"\n({zero_value_count} additional holdings with unknown USD value not shown)")

    user_msg = f"""
WALLET: {wallet}
TOTAL VALUE: ${total_value_usd:,.2f}
TOKENS AUDITED: {len(holdings_with_audits)}

HOLDINGS WITH RISK AUDITS:
{"".join(holding_lines) or "  No holdings found."}
"""
    return [
        {"role": "system", "content": PORTFOLIO_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]