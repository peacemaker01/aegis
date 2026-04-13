# ai/prompt_builder.py

SYSTEM_PROMPT = """\
You are a senior smart contract security auditor specialising in DeFi rug pull detection.
Analyse the Solidity source code and on-chain metadata provided.

Return ONLY valid JSON — no markdown, no text outside the JSON object.

Required schema:
{
  "risk_score": <float 0.0-10.0>,
  "recommendation": <"SAFE"|"CAUTION"|"AVOID">,
  "honeypot": <bool>,
  "mint_function": <bool>,
  "owner_renounced": <bool>,
  "proxy_pattern": <bool>,
  "hidden_owner": <bool>,
  "transfer_tax_modifiable": <bool>,
  "blacklist_function": <bool>,
  "max_tx_limit": <bool>,
  "liquidity_concerns": <bool>,
  "findings": [
    {
      "severity": <"CRITICAL"|"HIGH"|"MEDIUM"|"LOW"|"INFO">,
      "title": <string>,
      "description": <string>,
      "code_ref": <string or null>
    }
  ],
  "positive_signals": [<string>],
  "summary": <2-3 sentence plain English explanation>
}

Scoring guide:
  0-2 : Safe — no red flags
  3-4 : Low risk — minor issues
  5-6 : Medium risk — use caution
  7-8 : High risk — likely problematic
  9-10: Critical — definite rug / honeypot
"""

QA_SYSTEM_PROMPT = """\
You are a helpful smart contract audit assistant.
The user has already seen the full audit report with risk scores and security flags.
Answer their follow‑up questions based on the audit you previously performed.
Do NOT re‑audit the contract or repeat the full report.
Be concise, helpful, and conversational.
Do NOT return JSON unless specifically asked for structured data.
"""


def _format_holders(holders: list) -> str:
    if not holders:
        return "  Not available (Etherscan PRO required)"
    lines = []
    for h in holders[:5]:
        addr = h.get("TokenHolderAddress", "?")
        qty = h.get("TokenHolderQuantity", "?")
        lines.append(f"  {addr}  qty={qty}")
    return "\n".join(lines)


def build_audit_prompt(contract: dict) -> list[dict]:
    """Build the full OpenRouter message array for an audit."""
    source = contract.get("source", "") or ""
    if len(source) > 50000:
        source = source[:50000] + "\n\n[...SOURCE TRUNCATED FOR TOKEN LIMIT...]"

    user_msg = f"""
CHAIN:          {contract.get('chain', '?')} (chainId={contract.get('chain_id', '?')})
ADDRESS:        {contract.get('address', '?')}
VERIFIED:       {contract.get('verified', False)}
COMPILER:       {contract.get('compiler', 'unknown')}
IS PROXY:       {contract.get('proxy', False)}
IMPLEMENTATION: {contract.get('implementation', 'N/A')}
LICENSE:        {contract.get('license', 'None')}
DEPLOYER:       {contract.get('creator', 'unknown')}
DEPLOY TX:      {contract.get('tx_hash', 'unknown')}

TOKEN INFO:
  Name:         {contract.get('token_name', 'N/A')}
  Symbol:       {contract.get('token_symbol', 'N/A')}
  Total Supply: {contract.get('total_supply', 'N/A')}
  Holder Count: {contract.get('holders', 'N/A')}

TOP 5 HOLDERS:
{_format_holders(contract.get('top_holders', []))}

━━━ SOLIDITY SOURCE CODE ━━━
{source if source else "SOURCE NOT AVAILABLE — contract not verified on-chain."}
"""

    # Add Slither findings summary if available
    slither_findings = contract.get("slither_findings", [])
    if slither_findings:
        actual_findings = [f for f in slither_findings if not f.get("_slither_metadata")]
        if actual_findings:
            slither_summary = "\n\n━━━ SLITHER STATIC ANALYSIS FINDINGS ━━━\n"
            high = [f for f in actual_findings if f.get("severity") == "HIGH"]
            medium = [f for f in actual_findings if f.get("severity") == "MEDIUM"]
            low = [f for f in actual_findings if f.get("severity") == "LOW"]
            if high:
                slither_summary += f"\n  HIGH Severity ({len(high)}):\n"
                for f in high[:5]:
                    slither_summary += f"    - {f.get('detector', 'unknown')}: {f.get('description', '')[:100]}\n"
            if medium:
                slither_summary += f"\n  MEDIUM Severity ({len(medium)}):\n"
                for f in medium[:5]:
                    slither_summary += f"    - {f.get('detector', 'unknown')}: {f.get('description', '')[:100]}\n"
            if low:
                slither_summary += f"\n  LOW Severity ({len(low)}):\n"
                for f in low[:3]:
                    slither_summary += f"    - {f.get('detector', 'unknown')}: {f.get('description', '')[:100]}\n"
            if len(actual_findings) > 13:
                slither_summary += f"\n  ... and {len(actual_findings) - 13} more findings\n"
            user_msg += slither_summary

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]


def build_qa_prompt(
    contract: dict,
    history: list[dict],
    question: str,
    audit_result: dict = None,
) -> list[dict]:
    """
    Build a follow-up Q&A prompt WITHOUT re‑auditing the contract.
    Includes Slither findings from contract or audit_result.
    """
    # Try to get deployer address
    deployer = contract.get('creator') or contract.get('deployer')
    if not deployer and audit_result:
        deployer = audit_result.get('deployer') or audit_result.get('creator')
    if not deployer:
        deployer = "not provided in the contract data"

    context = f"""Contract: {contract.get('address', 'unknown')} on {contract.get('chain', 'unknown')}
Token: {contract.get('token_name', 'N/A')} ({contract.get('token_symbol', 'N/A')})
Deployer address: {deployer}"""

    audit_summary = ""
    if audit_result:
        risk = audit_result.get('risk_score', 'N/A')
        rec = audit_result.get('recommendation', 'N/A')
        summary = audit_result.get('summary', '')
        audit_summary = f"""
Previous audit result:
  Risk score: {risk}/10
  Recommendation: {rec}
  Summary: {summary}
"""

    # ── Get Slither findings (prioritise contract, then audit_result) ──
    slither_findings = contract.get("slither_findings", [])
    if not slither_findings and audit_result:
        slither_findings = audit_result.get("slither_findings", [])

    slither_summary = ""
    if slither_findings:
        actual_findings = [f for f in slither_findings if not f.get("_slither_metadata")]
        if actual_findings:
            slither_summary = "\nSlither static analysis findings (from the audit):\n"
            # Show most severe first
            high = [f for f in actual_findings if f.get("severity") == "HIGH"]
            medium = [f for f in actual_findings if f.get("severity") == "MEDIUM"]
            low = [f for f in actual_findings if f.get("severity") == "LOW"]
            for f in high[:3]:
                slither_summary += f"  - HIGH: {f.get('detector', 'unknown')} – {f.get('description', '')[:150]}\n"
            for f in medium[:5]:
                slither_summary += f"  - MEDIUM: {f.get('detector', 'unknown')} – {f.get('description', '')[:150]}\n"
            for f in low[:3]:
                slither_summary += f"  - LOW: {f.get('detector', 'unknown')} – {f.get('description', '')[:150]}\n"
            if len(actual_findings) > 11:
                slither_summary += f"  ... and {len(actual_findings) - 11} more findings\n"

    user_content = f"""Context from previously audited contract:

{context}
{audit_summary}
{slither_summary}

Answer the user's question based on the audit you already performed.
Do NOT re‑audit.
Be conversational and helpful.

User question: {question}"""

    messages = [
        {"role": "system", "content": QA_SYSTEM_PROMPT},
        {"role": "user", "content": user_content}
    ]
    if history:
        messages.extend(history[-4:])
    return messages
