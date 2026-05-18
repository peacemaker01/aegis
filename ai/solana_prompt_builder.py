# ai/solana_prompt_builder.py
"""
AI prompt builder for Solana token security analysis.
Correlates findings from RPC, RugCheck, GoPlus, and cross-chain data.
"""

SOLANA_SYSTEM_PROMPT = """\
You are not a financial advisor. You are a casino bouncer. Your job is to tell people
the house edge before they sit down. Being "nice" by lowering risk scores gets people hurt.

You are a DEFCON‑style risk detector for Solana tokens. Most tokens you see are Pump.fun launches with >99% failure rate.

HARD RULES:

PUMP.FUN DETECTION:
- If the token address ends with "pump" → THIS IS A PUMP.FUN TOKEN.
- All Pump.fun tokens start with BASE RISK 8/10.
- If liquidity = $0 OR insufficient data: 10/10 "INSTANT RUG – UNSWAPPABLE"
- If age <1hr OR liquidity <$30k: minimum 6/10 "GRADUATED BUT DANGEROUS"
- Top 10 holder % unknown? Assume >50% and flag "MAJORITY SUPPLY RISK"
- Never score a Pump.fun token below 4/10.

ABSOLUTE RULES (apply to all Solana tokens):
- Mint authority enabled = 10/10 "CAN INFLATE SUPPLY INFINITELY"
- Freeze authority enabled = 10/10 "YOUR TOKENS CAN BE FROZEN"
- $0 liquidity = 10/10 "INSTANT RUG – CANNOT SELL"
- LP not locked = HIGH RISK "LIQUIDITY CAN VANISH"
- LP locked <7 days: RISK minimum 9/10 "LIQUIDITY UNLOCKS IMMINENTLY"
- LP locked <30 days: RISK minimum 7/10 "LIQUIDITY UNLOCKS SOON"
- LP burned (locked forever): positive signal but does NOT reduce risk below 4/10 for Pump.fun

AGE + HOLDER HARD FLOORS (non-negotiable):
- Token age <48h AND LP unlocked → MINIMUM score 5.0, label must be "ELEVATED RISK" or higher
- Token age <24h AND LP unlocked → MINIMUM score 6.0, label must be "HIGH RISK" or higher
- Total holders <50 AND LP unlocked → MINIMUM score 5.0 (thin holder base = instant whale dump risk)
- Total holders <20 → add CRITICAL flag "Critically low holder count – single sell can crash price"
- Mint authority revoked AND freeze authority revoked = baseline hygiene only.
  These TWO factors combined CANNOT reduce a score by more than 0.5 total.
  Do NOT let revoked authorities pull a score below 5.0 if other risk factors are present.
- "CAUTION – MIXED SIGNALS" label is FORBIDDEN if ANY of these are true:
  LP unlocked, age <30 days, top10 holders >50%, total holders <100

SCORING CALIBRATION:
1. Pump.fun, 0 liq, 10min old → 10/10 INSTANT RUG
2. Pump.fun, $24k liq, LP burned, 10min old → 7/10 GRADUATED BUT DANGEROUS
3. SPL token, 24h old, LP unlocked, 20 holders, $114k liq → 7.0/10 HIGH RISK (NOT 2.5)
4. Any token, top 10 >55% → HIGH RISK (never "moderate")
5. "No data" on critical checks = COULD BE FRESH RUG FACTORY

FORBIDDEN: Safe, Low Risk, Moderate, Gem, Alpha, Smart Money
FORBIDDEN LABELS: "CAUTION – MIXED SIGNALS" for any token with LP unlocked or age <30 days

Required output:
{
  "risk_score": <float>,
  "risk_label": <string>,
  "recommendation": <"AVOID"|"CAUTION"|"INCONCLUSIVE">,
  "summary": <string leading with risk>,
  "flags": {
    "is_pumpfun": <bool>,
    "mint_authority_enabled": <bool>,
    "freeze_authority_enabled": <bool>,
    "lp_locked": <bool>,
    "lp_lock_days": <int or null>,
    "top10_pct": <float or null>
  },
  "findings": [{"severity":..., "title":..., "description":...}]
}

ALLOWED risk_label values (use ONLY these, no others):
"INSTANT RUG – UNSWAPPABLE" | "EXTREME RISK – LIKELY RUG" | "HIGH RISK – DEGEN GAMBLE" |
"HIGH RISK – UNVERIFIED" | "ELEVATED RISK – SPECULATIVE" | "GRADUATED BUT DANGEROUS" |
"MODERATE RISK – PROCEED WITH CARE" | "INSUFFICIENT DATA"
"""


def _format_cross_chain_profile(profile: dict) -> str:
    if not profile:
        return "No EVM deployer history found or data unavailable."
    total = profile.get("total_deployments", 0)
    if total == 0:
        return "No EVM deployments found for this wallet."
    chains = profile.get("chains_active", [])
    risk_signals = profile.get("risk_signals", {})
    deployments = profile.get("deployments", [])[:10]

    text = f"""
Total EVM Deployments: {total}
Active Chains: {', '.join(chains) if chains else 'none'}
Risk Signals:
  - Multi-chain deployer: {risk_signals.get('multi_chain_deployer', False)}
  - Unverified contracts: {risk_signals.get('has_unverified_contracts', False)} (count: {risk_signals.get('unverified_count', 0)})
  - Rapid deployment burst: {risk_signals.get('rapid_deployments', False)}
  - Low-holder contracts: {risk_signals.get('low_holder_contracts', 0)}

Recent Deployments (newest first):
"""
    for d in deployments[:5]:
        name = d.get("token_name") or d.get("contract_name") or "Unknown"
        chain = d.get("chain", "?").upper()
        verified = "✓" if d.get("verified") else "✗"
        holders = d.get("holder_count", "?")
        text += f"  - [{chain}] {name} (verified: {verified}, holders: {holders})\n"
    return text


def build_solana_scan_prompt(raw_data: dict, debug: bool = False) -> list[dict]:
    mint = raw_data.get("mint", "unknown")
    mint_info = raw_data.get("mint_info", {})
    rugcheck = raw_data.get("rugcheck", {})
    goplus = raw_data.get("goplus", {})
    holders = raw_data.get("holders", [])
    deployer_address = raw_data.get("deployer_address")
    cross_chain = raw_data.get("cross_chain_profile")
    contract_age_days = raw_data.get("contract_age_days")
    liquidity_depth = raw_data.get("liquidity_depth", 0)
    is_deep = raw_data.get("deep_scan", False)

    # Format mint authorities (on-chain ground truth)
    mint_auth = mint_info.get("mint_authority")
    freeze_auth = mint_info.get("freeze_authority")
    auth_text = f"Mint Authority: {'ENABLED' if mint_auth else 'DISABLED'} {f'({mint_auth[:10]}...)' if mint_auth else ''}\n"
    auth_text += f"Freeze Authority: {'ENABLED' if freeze_auth else 'DISABLED'} {f'({freeze_auth[:10]}...)' if freeze_auth else ''}\n"
    auth_text += f"Supply: {mint_info.get('supply', '?')} | Decimals: {mint_info.get('decimals', '?')}"

    # Format holder concentration
    holder_text = ""
    if holders:
        holder_text = f"Top {len(holders)} holders:\n"
        for h in holders[:10]:
            holder_text += f"  {h['address'][:8]}... : {h['percentage']}%\n"
        top10_pct = sum(h['percentage'] for h in holders[:10])
        holder_text += f"Top 10 holders control: {top10_pct:.1f}%"
    else:
        holder_text = "Holder data unavailable."

    # Format RugCheck
    rugcheck_text = ""
    if rugcheck and not rugcheck.get("error"):
        rugcheck_text = f"Score: {rugcheck.get('score')} | Verdict: {rugcheck.get('verdict')} | LP Locked: {rugcheck.get('lp_locked_pct')}%\n"
        for f in rugcheck.get("findings", [])[:5]:
            rugcheck_text += f"  - [{f.get('severity')}] {f.get('title')}: {f.get('description')[:100]}\n"
    else:
        rugcheck_text = "No data or error."

    # Solsniffer formatting removed

    # Add deep metrics
    deep_metrics = ""
    if is_deep:
        deep_metrics = f"""
━━━ DEEP SCAN METRICS ━━━
Contract Age: {contract_age_days if contract_age_days is not None else 'unknown'} days
Total Liquidity Across Pools: ${liquidity_depth:,.2f}
These metrics help assess the token's maturity and market depth. Newer tokens with low liquidity are higher risk.
"""

    cross_chain_text = _format_cross_chain_profile(cross_chain) if cross_chain else "Cross-chain analysis not available."

    lp_lock_days = raw_data.get("lp_lock_days")
    lp_lock_text = ""
    if lp_lock_days is not None:
        lp_lock_text = f"LP Locked for: {lp_lock_days} days\n"
        if lp_lock_days < 7:
            lp_lock_text += "⚠️ LP UNLOCKS IN <7 DAYS – HIGH RISK\n"
        elif lp_lock_days < 30:
            lp_lock_text += "⚠️ LP UNLOCKS IN <30 DAYS – ELEVATED RISK\n"
    elif rugcheck.get("lp_locked_pct") is not None:
        lp_lock_text = f"LP Locked Percent: {rugcheck.get('lp_locked_pct')}%\n"

    user_msg = f"""
━━━ ON-CHAIN DATA (Solana RPC) ━━━
Token: {mint}
{auth_text}
{holder_text}
{lp_lock_text}
{deep_metrics}

━━━ RUGCHECK.XYZ ━━━
{rugcheck_text}

━━━ CROSS-CHAIN DEPLOYER FORENSICS (EVM) ━━━
Deployer Address: {deployer_address or 'unknown'}
{cross_chain_text}

Based on ALL the above, produce a unified risk assessment.
If deep scan metrics are present, use them to refine the risk score. Newer contracts with low liquidity should receive higher risk scores.
If the deployer has a history of rug pulls or suspicious EVM activity, this MUST be reflected in the risk score and flagged prominently.
"""
    return [
        {"role": "system", "content": SOLANA_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
