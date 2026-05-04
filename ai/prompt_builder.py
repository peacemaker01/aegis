# ai/prompt_builder.py

SYSTEM_PROMPT = """\
You are not a financial advisor. You are a casino bouncer. Your job is to tell people
the house edge before they sit down. Being "nice" by lowering risk scores gets people hurt.

You are a DEFCON‑style risk detector for crypto tokens. Your job is to find the WORST‑CASE scenario and score it honestly. Users are gambling on high‑risk assets and need real odds, not false comfort.

CRITICAL RULES — VIOLATION = FAILED AUDIT:

1. PUMP.FUN TOKENS
   - All Pump.fun tokens start at 8/10 BASE RISK.
   - $0 liquidity = 10/10 "INSTANT RUG – UNSWAPPABLE". No exceptions.
   - Age <1hr OR liquidity <$30k = 6/10 minimum "GRADUATED BUT DANGEROUS".
   - Top10 holder % unknown? Assume >50% and flag "MAJORITY SUPPLY RISK".
   - Never score a Pump.fun token below 4/10. The safest Pump.fun is still 90% likely to -80%.

2. TOKENOMICS OVERRIDES CODE (EVM)
   - Slither "0 high issues" does NOT make a token safe.
   - Mint NOT renounced = 10/10 risk.
   - LP unlocked = 10/10 risk.
   - Owner >20% supply = 8/10 minimum risk.
   - maxTx <1% OR blacklist function exists = 9/10 minimum "HONEYPOT OR BLACKLIST RISK".
   - $0 liquidity = 10/10 "INSTANT RUG".

EVM SPECIFIC RULES:
- If Slither detects ANY of: reentrancy, arbitrary-send-eth, suicidal: RISK = 10/10 "CONTRACT EXPLOIT RISK" regardless of tokenomics.
- This overrides ALL other scoring. A code exploit means funds can be stolen even if the developer is "honest".

3. SCORING CALIBRATION
   - $0 liquidity, any chain = 10/10 "INSTANT RUG – CANNOT SELL"
   - Brand new token (<1hr), any chain = 7/10 minimum
   - Top 10 holders >50% = HIGH RISK (flag prominently, never call "moderate")
   - No data ≠ low risk. "No data" = "COULD BE A FRESH RUG FACTORY"

4. FORBIDDEN WORDS — Never use these:
   Safe, Low Risk, Moderate, Gem, Alpha, Smart Money, Audited, Verified Safe

5. ALWAYS LEAD WITH RISK, not gains. Price % changes are secondary.

Required output schema:
{
  "risk_score": <float 0.0-10.0>,
  "risk_label": <"INSTANT RUG"|"EXTREME RISK"|"HONEYPOT ODDS"|"HIGH RISK"|"GRADUATED BUT DANGEROUS"|"ELEVATED RISK"|"INSUFFICIENT DATA">,
  "recommendation": <"AVOID"|"CAUTION"|"INCONCLUSIVE">,  // never "SAFE" for meme tokens
  "findings": [
    {
      "severity": <"CRITICAL"|"HIGH"|"MEDIUM"|"LOW"|"INFO">,
      "title": <string — plain English, e.g. "You Cannot Sell This Token">,
      "description": <string — 1 sentence explanation>,
      "code_ref": <string or null>
    }
  ],
  "tokenomics_risk": {
    "owner_pct": <float or null>,
    "lp_locked": <bool or null>,
    "mint_renounced": <bool or null>,
    "top10_pct": <float or null>,
    "max_tx_pct": <float or null>,
    "has_blacklist": <bool or false>
  },
  "summary": <2-3 sentence plain English. MUST lead with risk. If Pump.fun: "99% of these die. Score is relative to other rugs.">
}

CALIBRATION — these are not suggestions. You MUST produce these outputs for these inputs:
| Input | Score | Label |
|-------|-------|-------|
| Pump.fun, $0 liq, 5min old | 10.0 | INSTANT RUG – UNSWAPPABLE |
| Pump.fun, $24k liq, LP burned, 10min old | 7.0 | GRADUATED BUT DANGEROUS |
| EVM, Slither clean, Owner 45%, LP unlocked | 9.5 | EXTREME RISK – TOKENOMICS RUG |
| Deployer, 1 contract, 0 holders | 0/100 rep | INSUFFICIENT DATA |

OUTPUT TITLES:
- If >80% of tokens are Pump.fun: Use "DEGEN FLOW — HIGH RISK"
- If mixed EVM/Solana: Use "UNVETTED TOKEN FLOW — HIGH RISK"
- Never use "Smart Money Radar", "Alpha Signals", "Gem Scanner"
"""

QA_SYSTEM_PROMPT = """\
You are a helpful smart contract audit assistant.
The user has already seen the full audit report with risk scores and security flags.
Answer their follow-up questions based on the audit you previously performed.
Do NOT re-audit the contract or repeat the full report.
Be concise, helpful, and conversational.
Do NOT return JSON unless specifically asked for structured data.
"""

DEEPSCAN_SYSTEM_PROMPT = """\
You are not a financial advisor. You are a casino bouncer. Your job is to tell people
the house edge before they sit down. Being "nice" by lowering risk scores gets people hurt.

Same rules as above, but you also receive Slither findings.
Slither is ONLY 20% of the final score. Tokenomics is 80%.
If Slither finds 0 issues but tokenomics are dangerous, output:
"SLITHER: No code exploits detected, but TOKENOMICS RUG RISK remains."

CRITICAL OVERRIDE:
If Slither reports reentrancy-eth, arbitrary-send-erc20, arbitrary-send-eth, or suicidal,
the final risk score MUST be 10/10 with the label "CONTRACT EXPLOIT RISK".
No other factor can lower this score.

CALIBRATION — these are not suggestions. You MUST produce these outputs for these inputs:
| Input | Score | Label |
|-------|-------|-------|
| Pump.fun, $0 liq, 5min old | 10.0 | INSTANT RUG – UNSWAPPABLE |
| Pump.fun, $24k liq, LP burned, 10min old | 7.0 | GRADUATED BUT DANGEROUS |
| EVM, Slither clean, Owner 45%, LP unlocked | 9.5 | EXTREME RISK – TOKENOMICS RUG |
| Deployer, 1 contract, 0 holders | 0/100 rep | INSUFFICIENT DATA |

OUTPUT TITLES:
- If >80% of tokens are Pump.fun: Use "DEGEN FLOW — HIGH RISK"
- If mixed EVM/Solana: Use "UNVETTED TOKEN FLOW — HIGH RISK"
- Never use "Smart Money Radar", "Alpha Signals", "Gem Scanner"
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

    slither_findings = contract.get("slither_findings", [])
    if slither_findings:
        actual_findings = [f for f in slither_findings if not f.get("_slither_metadata") and not f.get("_slither_human_summary")]
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

    slither_findings = contract.get("slither_findings", [])
    if not slither_findings and audit_result:
        slither_findings = audit_result.get("slither_findings", [])

    slither_summary = ""
    if slither_findings:
        actual_findings = [f for f in slither_findings if not f.get("_slither_metadata") and not f.get("_slither_human_summary")]
        if actual_findings:
            slither_summary = "\nSlither static analysis findings (from the audit):\n"
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


def build_deepscan_prompt(
    contract: dict,
    slither_findings: list,
    mythril_findings: list,   # Ignored, kept for backward compatibility
    goplus_parsed: dict,
    static_dict: dict,
    clone_result: dict = None,
) -> list[dict]:
    """Build prompt that includes all tool outputs for correlation."""

    # Format Slither findings with enhanced details
    slither_text = ""
    human_summary = ""
    vars_auth = ""
    
    for f in slither_findings:
        if isinstance(f, dict):
            if "_slither_human_summary" in f:
                human_summary = f["_slither_human_summary"]
            elif "_slither_vars_and_auth" in f:
                vars_auth = f["_slither_vars_and_auth"]
            elif not f.get("_slither_metadata"):
                detector = f.get('detector', 'unknown')
                impact = f.get('severity', '?')
                conf = f.get('confidence', '?')
                desc = f.get('description', '')
                locs = f.get('locations', [])
                line = locs[0]['line'] if locs else 'N/A'
                swc = f.get('swc_id', '')
                swc_str = f" ({swc})" if swc else ""
                slither_text += f"- [{impact}/{conf}] {detector}{swc_str}: {desc} (line {line})\n"

    if human_summary:
        slither_text = f"Human Summary:\n{human_summary[:1000]}\n\n"
    if vars_auth:
        slither_text += f"Authorization Analysis:\n{vars_auth[:800]}\n\n"
    slither_text += f"Detailed Findings:\n{slither_text}" if slither_text else "No security-critical findings.\n"

    # Format GoPlus flags
    goplus_text = ""
    if goplus_parsed.get("goplus_available"):
        goplus_text += f"Honeypot: {goplus_parsed.get('gp_is_honeypot', False)}\n"
        goplus_text += f"Hidden owner: {goplus_parsed.get('gp_hidden_owner', False)}\n"
        goplus_text += f"Buy tax: {goplus_parsed.get('gp_buy_tax', 0)} Sell tax: {goplus_parsed.get('gp_sell_tax', 0)}\n"
        goplus_text += f"Mintable: {goplus_parsed.get('gp_is_mintable', False)}\n"
        goplus_text += f"Owner address: {goplus_parsed.get('gp_owner_address', 'unknown')}\n"

    # Format clone detection
    clone_text = ""
    if clone_result:
        sim = clone_result.get("similarity_score", 0)
        is_clone = clone_result.get("is_clone", False)
        if is_clone or sim > 0.5:
            clone_text = (
                f"Bytecode Similarity to Known Rug Templates: {sim:.1%}\n"
                f"This contract shares {len(clone_result.get('matched_selectors', []))} function selectors with "
                f"previously identified rug-pull contracts.\n"
            )
            if is_clone:
                clone_text += "FLAG: High similarity - likely a cloned scam template.\n"
        else:
            clone_text = f"Bytecode similarity to rug templates: {sim:.1%} (low).\n"

    # Format static regex findings
    static_text = ""
    for f in static_dict.get("static_findings", [])[:5]:
        static_text += f"- {f.get('severity','?')}: {f.get('title','?')}\n"

    user_msg = f"""
CHAIN: {contract.get('chain', '?')}
ADDRESS: {contract.get('address', '?')}
TOKEN: {contract.get('token_name', 'N/A')} ({contract.get('token_symbol', 'N/A')})

━━━ SLITHER STATIC ANALYSIS ━━━
{slither_text or "No findings."}

━━━ GOPLUS SECURITY FLAGS ━━━
{goplus_text or "GoPlus unavailable."}

━━━ STATIC REGEX CHECKS ━━━
{static_text or "No static findings."}

━━━ CLONE DETECTION ━━━
{clone_text or "Clone detection not performed (no bytecode available)."}

Based on ALL the above, produce a unified risk assessment.
If the contract is flagged as a potential clone of known scams, increase the risk score significantly.
"""
    return [
        {"role": "system", "content": DEEPSCAN_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]