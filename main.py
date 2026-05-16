#!/usr/bin/env python3
"""
AEGIS – AI-Powered Security Scanner for Crypto
Scans Solana, EVM, and cross-chain for rug pulls, malicious contracts, and deployer forensics.
"""

import asyncio, json, logging, os, html, aiohttp
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from core.config import config, logger

# ────────────────────────── FastAPI ──────────────────────────
app = FastAPI()

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    if app.state.application:
        await app.state.application.process_update(
            Update.de_json(data, app.state.application.bot)
        )
    return {"ok": True}

# ────────────────────────── Setup ──────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger.warning("Aegis starting up...")

SCAN_SEMAPHORE = asyncio.Semaphore(10)
DEBUG = True   # Set to False for production

def escape_html(text: str) -> str: return html.escape(text or "")

# ───────────────────── Private‑chat guard ─────────────────────
async def _require_private_chat(update: Update) -> bool:
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text(
            "🛡️ Aegis works best in private chat. Message me directly: @YourAegisBot"
        )
        return False
    return True

# ───────────────────── Subscription guard ─────────────────────
async def require_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    db_user = await get_or_create_user(user.id, user.username or "", user.first_name or "")
    allowed, reason = can_use_service(db_user)
    if allowed:
        context.user_data["db_user"] = db_user
        return True
    await update.effective_message.reply_text(
        "⛔ This premium feature requires an active subscription.\n\n"
        "💎 Aegis Premium — $14.99/month\n"
        " • Unlimited scans on 10+ chains\n"
        " • AI-powered security verdicts\n"
        " • Smart Money Radar\n"
        " • New Token Scout\n"
        " • Deployer Forensics\n"
        " • Professional PDF Reports\n\n"
        "Start your 3‑day free trial or subscribe now: /subscribe",
        parse_mode="HTML",
    )
    return False

# ───────────────────── Helper keyboards ─────────────────────
def _get_token_chain(token: dict) -> str:
    chain = token.get("chain")
    if isinstance(chain, dict):
        slug = chain.get("slug") or chain.get("name") or ""
        return slug.upper()[:6]
    if isinstance(chain, str) and chain: return chain.upper()[:6]
    return "?"

def _build_token_keyboard(tokens: list, page: int = 0) -> list:
    per_page, total_pages = 30, max(1, (len(tokens) + 29) // 30)
    start, end = page * per_page, min(page * per_page + per_page, len(tokens))
    page_tokens = tokens[start:end]
    keyboard, row = [], []
    for i, t in enumerate(page_tokens):
        symbol = t.get("token_symbol") or t.get("symbol") or t.get("name") or "?"
        chain_label = _get_token_chain(t)
        global_idx = start + i
        row.append(InlineKeyboardButton(f"{symbol} ({chain_label})", callback_data=f"audit_idx_{global_idx}"))
        if len(row) == 3: keyboard.append(row); row = []
    if row: keyboard.append(row)
    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton("◀️ Previous", callback_data=f"wallet_page_{page-1}"))
    if end < len(tokens): nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"wallet_page_{page+1}"))
    if nav_row: keyboard.append(nav_row)
    return keyboard

ALLOWED_RISK_LABELS = {
    "INSTANT RUG – UNSWAPPABLE",
    "EXTREME RISK – LIKELY RUG",
    "EXTREME RISK – TOKENOMICS RUG",
    "HIGH RISK – DEGEN GAMBLE",
    "HIGH RISK – UNVERIFIED",
    "ELEVATED RISK – SPECULATIVE",
    "GRADUATED BUT DANGEROUS",
    "MODERATE RISK – PROCEED WITH CARE",
    "INSUFFICIENT DATA",
}

def _validate_and_correct_result(result: dict, raw_data: dict | None = None) -> dict:
    """
    Post-processing guard: catches score inversions, hallucinated labels,
    and structural inconsistencies before any result reaches the user.

    raw_data keys used (all optional):
      lp_locked, top10_pct, age_hours, holder_count, liquidity_usd
    """
    if not isinstance(result, dict):
        return result

    score = result.get("risk_score")
    label = result.get("risk_label", "")
    flags = result.get("findings") or result.get("flags") or []

    # ── 1. Clamp score to valid range ─────────────────────────────────
    if isinstance(score, (int, float)):
        score = max(0.0, min(10.0, float(score)))
        result["risk_score"] = round(score, 1)

    # ── 2. Reject hallucinated labels ─────────────────────────────────
    if label and label not in ALLOWED_RISK_LABELS:
        # Derive a safe label from the score
        if isinstance(score, float):
            if score >= 9.0:   result["risk_label"] = "EXTREME RISK – LIKELY RUG"
            elif score >= 7.0: result["risk_label"] = "HIGH RISK – DEGEN GAMBLE"
            elif score >= 5.0: result["risk_label"] = "ELEVATED RISK – SPECULATIVE"
            else:              result["risk_label"] = "INSUFFICIENT DATA"
        else:
            result["risk_label"] = "INSUFFICIENT DATA"

    # ── 3. Structural inversion check (requires raw_data context) ─────
    if raw_data and isinstance(score, float):
        lp_locked    = raw_data.get("lp_locked", False)
        top10_pct    = raw_data.get("top10_pct") or 0
        age_hours    = raw_data.get("age_hours") or (raw_data.get("age_minutes", 0) / 60)
        holder_count = raw_data.get("holder_count") or 0
        liq          = raw_data.get("liquidity_usd") or raw_data.get("liq") or 0

        structural_risks = sum([
            not lp_locked,
            top10_pct > 50,
            age_hours <= 48,
            (0 < holder_count < 50),
        ])

        # If 3+ structural risks and score < 5 → something went wrong
        if structural_risks >= 3 and score < 5.0:
            result["risk_score"] = 5.0
            result["risk_label"] = "HIGH RISK – UNVERIFIED"
            result.setdefault("_validation_note", "Score raised by post-processor: structural risk floor")

        # If 2+ structural risks and score < 4 → floor it
        elif structural_risks >= 2 and score < 4.0:
            result["risk_score"] = 4.0
            result["_validation_note"] = "Score raised by post-processor: structural risk floor"

    return result


def format_degen_report(chain: str, address: str, result: dict) -> str:
    score = result.get('risk_score', 'N/A')
    rec = result.get('recommendation', 'CAUTION')
    
    degen_lines = []
    degen_lines.append(f"🎰 <b>{chain.upper()} RISK: {score}/10 \"{rec}\"</b>")

    raw = result.get('_raw', {})
    
    # Code check
    slither_findings = raw.get('slither', [])
    actual_slither = [f for f in slither_findings if not f.get('_slither_metadata')]
    if actual_slither:
        high_slither = [f for f in actual_slither if f.get('severity') == 'HIGH']
        if high_slither:
            degen_lines.append(f"🔍 <b>CODE:</b> {high_slither[0].get('detector','?')}: {high_slither[0].get('description','')[:80]}")
        else:
            degen_lines.append(f"🔍 <b>CODE:</b> No exploits, but TOKENOMICS may still be dangerous")
    else:
        # Solana – show authorities
        mint_info = raw.get('mint_info', {})
        if mint_info:
            mint_auth = "ENABLED ⚠️" if mint_info.get('mint_authority') else "Disabled"
            freeze_auth = "ENABLED ⚠️" if mint_info.get('freeze_authority') else "Disabled"
            degen_lines.append(f"🔍 <b>CODE:</b> Mint: {mint_auth} | Freeze: {freeze_auth}")
        else:
            degen_lines.append(f"🔍 <b>CODE:</b> Unable to verify authorities")

    # Tokenomics / Bag Alert
    top10_pct = result.get('flags', {}).get('top10_pct') or (
        sum(h.get('percentage', 0) for h in raw.get('holders', [])[:10])
        if raw.get('holders') else None
    )
    lp_lock_days = result.get('flags', {}).get('lp_lock_days')
    if lp_lock_days is not None:
        if lp_lock_days == 0 or lp_lock_days > 365*10:   # effectively burned
            lp_status = "🔒 Burned (locked forever)"
        elif lp_lock_days < 7:
            lp_status = f"⚠️ Unlocks in {lp_lock_days}d"
        elif lp_lock_days < 30:
            lp_status = f"🔓 Unlocks in {lp_lock_days}d"
        else:
            lp_status = f"✅ Locked {lp_lock_days}d"
    else:
        lp_status = result.get('flags', {}).get('lp_locked', False)
        lp_status = "🔒 Locked" if lp_status else "🔓 Unlocked"
    
    if top10_pct is not None:
        degen_lines.append(f"💼 <b>TOKENOMICS:</b> Top10 hold {top10_pct:.0f}% | {lp_status}")
    else:
        degen_lines.append(f"💼 <b>TOKENOMICS:</b> {lp_status}")

    # Bag alert
    holder_count = result.get('flags', {}).get('holder_count') or len(raw.get('holders', []))
    liq = raw.get('liquidity_usd') or raw.get('liq') or 0
    bag_alert = ""
    if holder_count and holder_count < 100:
        bag_alert = f"👛 {holder_count} holders"
    if liq > 0:
        if bag_alert:
            bag_alert += f' | LIQ: ${liq:,.0f}'
        else:
            bag_alert = f"LIQ: ${liq:,.0f}"

    lp_status = f'Locked {lp_lock_days}d' if lp_locked else 'Unlocked'
    if age_hours is not None and age_hours != 999:
        age_str = f'{int(age_hours)}h' if age_hours < 72 else f'{int(age_hours / 24)}d'
    else:
        age_str = '?'

    risk_flags = [f for f in flags if not f.startswith(('Deep liquidity', 'Long track record', 'Widely distributed', 'LP locked for', 'Mint authority revoked', 'Freeze authority revoked', 'Owner holds only'))]
    _priority_prefixes = ('Critically low', 'Zero liquidity', 'Very thin', 'Brand new', 'Very new', 'Mint authority live', 'Freeze authority live', 'CRITICAL concentration', 'Highly concentrated', 'LP unlocked', 'Floor:')
    priority_flags = [f for f in risk_flags if any(f.startswith(p) for p in _priority_prefixes)]
    main_risk = priority_flags[0] if priority_flags else (risk_flags[0] if risk_flags else (flags[0] if flags else 'Insufficient data – see flags below'))

    if bag_alert:
        degen_lines.append(f"🎯 <b>KEY RISK:</b> {main_risk} | {bag_alert}")
    else:
        degen_lines.append(f"🎯 <b>KEY RISK:</b> {main_risk}")

    # Links
    if chain.lower() == 'solana':
        degen_lines.append(f"<a href='https://solscan.io/token/{address}'>📍 SolScan</a> | <a href='https://rugcheck.xyz/tokens/{address}'>🔍 RugCheck</a>")
    else:
        explorer = 'etherscan.io' if chain.lower() == 'ethereum' else f'{chain.lower()}.etherscan.io'
        degen_lines.append(f"<a href='https://{explorer}/token/{address}'>📍 Explorer</a>")

    text = "\n".join(degen_lines)
    if result.get('findings'):
        text += f"\n\n📋 <b>Findings:</b>\n"
        for finding in result.get('findings', [])[:3]:
            sev = finding.get('severity', '?')
            title = finding.get('title', '?')
            text += f"  [{sev}] {title}\n"
    
    return text

async def calculate_degen_risk_solana(raw: dict, ca: str, fast_mode: bool) -> dict:
    """
    Unified risk calculation for Solana tokens.
    Uses strict floor rules to prevent score inversions.
    """
    from core.db import db_set_cache, db_get_cache
    
    # Check cache
    cache_key = f"solana_risk_{ca}"
    if fast_mode:
        cached = await db_get_cache(cache_key)
        if cached:
            return json.loads(cached)
    
    mint_info = raw.get('mint_info', {})
    holders = raw.get('holders', [])
    rugcheck = raw.get('rugcheck', {})
    
    mint_revoked = not mint_info.get('mint_authority')
    freeze_revoked = not mint_info.get('freeze_authority')
    lp_locked = raw.get('lp_locked', False)
    lp_lock_days = raw.get('lp_lock_days')
    age_hours = raw.get('age_hours')
    holder_count = len(holders) if holders else 0
    liq = raw.get('liquidity_usd', 0)
    top10_pct = sum(h.get('percentage', 0) for h in holders[:10]) if holders else None
    
    score = 0.0
    flags = []
    
    # ---- Liquidity ----
    if liq == 0:
        score += 10.0
        flags.append('Zero liquidity – cannot sell')
    elif liq < 5000:
        score += 3.0
        flags.append(f'Very thin liquidity: ${liq:,.0f}')
    elif liq < 30000:
        score += 1.5
        flags.append(f'Low liquidity: ${liq:,.0f}')
    elif liq > 500000:
        score -= 1.0
        flags.append(f'Deep liquidity: ${liq:,.0f}')
    
    # ---- Age ----
    if age_hours is not None and age_hours != 999:
        if age_hours < 1:
            score += 2.0
            flags.append('Brand new (<1 hour)')
        elif age_hours <= 24:          # FIX: was < 24, misses tokens at exactly 24h
            score += 1.5
            flags.append(f"Very new ({int(age_hours)}h)")
        elif age_hours < 720:
            score += 0.5
            flags.append(f'Recent ({int(age_hours)}h)')
    
    # ---- Concentration ----
    if top10_pct is not None:
        if top10_pct > 80:
            score += 3.0
            flags.append(f'CRITICAL concentration: Top10 hold {top10_pct:.0f}%')
        elif top10_pct > 60:
            score += 2.0
            flags.append(f'Highly concentrated: Top10 hold {top10_pct:.0f}%')
        elif top10_pct > 50:
            score += 1.0
            flags.append(f'Concentrated: Top10 hold {top10_pct:.0f}%')
        else:
            score -= 1.0
            flags.append(f'Widely distributed: Top10 hold {top10_pct:.0f}%')

    # ---- Holder count (absolute) ----
    # Total holder count is independent of top10% — 20 holders means
    # a single whale sell can crater the price regardless of concentration %.
    if 0 < holder_count < 20:
        score += 2.0
        flags.append(f'Critically low holder count: only {holder_count} holders')
    elif 0 < holder_count < 50:
        score += 1.5
        flags.append(f'Low holder count: {holder_count} holders')

    # ---- LP lock ----
    if not lp_locked and not mint_revoked and not freeze_revoked:
        pass   # normal for managed tokens
    elif not lp_locked:
        score += 1.5
        flags.append('LP unlocked – can be removed')
    elif lp_lock_days is not None:
        if lp_lock_days < 7:
            score += 2.0
            flags.append(f"LP locked for {lp_lock_days}d")

    # ---- Authorities ----
    # Revoked authorities are baseline hygiene — grant only a small credit
    # so they cannot cancel out genuine risk signals like unlocked LP or age.
    if not mint_revoked:
        score += 2.0
        flags.append('Mint authority live – supply can be increased')
    else:
        score -= 0.25   # FIX: was -1.0, too much credit
        flags.append('Mint authority revoked')

    if not freeze_revoked:
        score += 2.0
        flags.append('Freeze authority live – tokens can be frozen')
    else:
        score -= 0.25   # FIX: was -1.0
        flags.append('Freeze authority revoked')

    # ---- Floor Rules ----
    # Rule 1: New token (≤48h) with unlocked LP cannot score below 5.0
    if age_hours is not None and age_hours != 999 and age_hours <= 48 and not lp_locked:
        if score < 5.0:
            score = 5.0
            flags.append('Floor: New token with unlocked LP — minimum HIGH RISK')

    # Rule 2: LP unlocked + top10 >50% → minimum 4.0
    if not lp_locked and top10_pct is not None and top10_pct > 50:
        if score < 4.0:
            score = 4.0
            flags.append('Structural floor: Unlocked LP + concentrated holders')

    # ---- Final clamping ----
    score = max(0.0, min(10.0, score))

    # ---- Structural risk count (prevents misleadingly low labels) ----
    _structural_risks = sum([
        not lp_locked,
        (top10_pct or 0) > 50,
        (age_hours or 999) <= 48,
        (0 < holder_count < 50),
    ])

    # ---- Label ----
    if liq == 0:
        label = 'INSTANT RUG – UNSWAPPABLE'
    elif score >= 9.0:
        label = 'EXTREME RISK – LIKELY RUG'
    elif score >= 7.0:
        label = 'HIGH RISK – DEGEN GAMBLE'
    elif score >= 5.0:
        label = 'ELEVATED RISK – SPECULATIVE'
    elif _structural_risks >= 2:
        # 2+ structural risk factors → never show a reassuring label
        label = 'HIGH RISK – UNVERIFIED'
    elif score >= 2.5:
        label = 'MODERATE RISK – PROCEED WITH CARE'
    elif score >= 1.0:
        label = 'LOW RISK – ESTABLISHED ASSET'
    else:
        label = 'INSUFFICIENT DATA'
    
    # ---- Recommendation ----
    if score >= 7.0:
        rec = "AVOID"
    elif score >= 4.0:
        rec = "CAUTION"
    else:
        rec = "INCONCLUSIVE"
    
    result = {
        "risk_score": round(score, 1),
        "risk_label": label,
        "recommendation": rec,
        "flags": {
            "mint_authority_enabled": not mint_revoked,
            "freeze_authority_enabled": not freeze_revoked,
            "lp_locked": lp_locked,
            "lp_lock_days": lp_lock_days,
            "top10_pct": top10_pct,
            "holder_count": holder_count,
        },
        "findings": [{"severity": "INFO", "title": f, "description": ""} for f in flags],
        "_raw": raw,
    }
    
    # Post-processing validation
    result = _validate_and_correct_result(result, {
        "lp_locked": lp_locked,
        "top10_pct": top10_pct or 0,
        "age_hours": age_hours,
        "holder_count": holder_count,
        "liquidity_usd": liq,
    })
    
    # Cache result
    await db_set_cache(cache_key, json.dumps(result), ttl=3600)
    
    return result


def _calculate_degenflow_risk_full(token: dict) -> dict:
    """Calculate full risk profile for a token (EVM or Solana cross-chain)."""
    
    mint_revoked = token.get("mint_authority_revoked", False)
    freeze_revoked = token.get("freeze_authority_revoked", False)
    lp_locked = token.get("lp_locked", False)
    lp_lock_days = token.get("lp_lock_days")
    age_hours = token.get("age_hours", 0)
    holder_count = token.get("holder_count", 0)
    liq = token.get("liquidity_usd", 0)
    top10_pct = token.get("top10_pct", 0)
    
    score = 0.0
    flags = []
    code_summary = ""
    
    # ---- Liquidity ----
    if liq == 0:
        score += 10.0
        flags.append('Zero liquidity – cannot sell')
    elif liq < 5000:
        score += 3.0
        flags.append(f'Very thin liquidity: ${liq:,.0f}')
    elif liq < 30000:
        score += 1.5
        flags.append(f'Low liquidity: ${liq:,.0f}')
    elif liq > 500000:
        score -= 1.0
        flags.append(f'Deep liquidity: ${liq:,.0f}')
    
    # ---- Age ----
    if age_hours < 1:
        score += 2.0
        flags.append('Brand new (<1 hour)')
    elif age_hours <= 24:
        score += 1.5
        flags.append(f'Very new ({int(age_hours)}h)')
    elif age_hours < 720:   # 30 days
        score += 0.5
        flags.append(f'Recent ({int(age_hours)}h)')
    
    # ---- Concentration ----
    if top10_pct > 80:
        score += 3.0
        flags.append(f'CRITICAL concentration: Top10 hold {top10_pct:.0f}%')
    elif top10_pct > 60:
        score += 2.0
        flags.append(f'Highly concentrated: Top10 hold {top10_pct:.0f}%')
    elif top10_pct > 50:
        score += 1.0
        flags.append(f'Concentrated: Top10 hold {top10_pct:.0f}%')
    else:
        score -= 1.0
        flags.append(f'Widely distributed: Top10 hold {top10_pct:.0f}%')
    
    # ---- Code check (EVM only) ----
    slither = token.get("slither", [])
    try:
        high_issues = [s for s in slither if s.get('severity') == 'HIGH']
        if high_issues:
            score += 3.5
            code_summary = f"⚠️ {len(high_issues)} HIGH severity issues"
            for issue in high_issues[:2]:
                flags.append(f"[CODE] {issue.get('detector')}: {issue.get('description')[:80]}")
        else:
            code_summary = "✅ No critical exploits"
    except:
        pass

    # ---- Holder count (absolute) ----
    # Low total holder count is an independent concentration risk signal.
    # < 50 holders = very early / whale-dominated regardless of top10 %.
    if 0 < holder_count < 20:
        score += 2.0
        flags.append(f'Critically low holder count: only {holder_count} holders')
    elif 0 < holder_count < 50:
        score += 1.5
        flags.append(f'Low holder count: {holder_count} holders')

    # ---- Authorities ----
    # Mint/freeze revoked is baseline hygiene in Solana — expected on any legitimate token.
    # Grant only a small credit (-0.25 each) so it cannot cancel genuine risk signals.
    if mint_revoked is False:
        score += 2.0
        flags.append('Mint authority live – supply can be increased')
    elif mint_revoked is True:
        score -= 0.25
        flags.append('Mint authority revoked')

    if freeze_revoked is False:
        score += 2.0
        flags.append('Freeze authority live – tokens can be frozen')
    elif freeze_revoked is True:
        score -= 0.25
        flags.append('Freeze authority revoked')

    # ---- Floor Rules ----
    # Rule 1: New token (<48h) with unlocked LP cannot score below 5.0
    if age_hours <= 48 and not lp_locked:
        if score < 5.0:
            score = 5.0
            flags.append('Floor: New token with unlocked LP — minimum HIGH RISK')

    # Rule 2: Unlocked LP + top10 >50% cannot score below 4.0
    if not lp_locked and top10_pct > 50:
        if score < 4.0:
            score = 4.0
            flags.append('Structural floor: Unlocked LP + concentrated holders')

    # ---- Final clamping ----
    score = max(0.0, min(10.0, score))

    # ---- Structural risk count (prevents misleadingly low labels) ----
    _structural_risks = sum([
        not lp_locked,
        top10_pct > 50,
        age_hours <= 48,
        (0 < holder_count < 50),
    ])

    # ---- Label ----
    if liq == 0:
        label = 'INSTANT RUG – UNSWAPPABLE'
    elif score >= 9.0:
        label = 'EXTREME RISK – LIKELY RUG'
    elif score >= 7.0:
        label = 'HIGH RISK – DEGEN GAMBLE'
    elif score >= 5.0:
        label = 'ELEVATED RISK – SPECULATIVE'
    elif _structural_risks >= 2:
        # 2+ structural risk factors → never show low-risk label regardless of score
        label = 'HIGH RISK – UNVERIFIED'
    elif score >= 2.5:
        label = 'MODERATE RISK – PROCEED WITH CARE'
    elif score >= 1.0:
        label = 'LOW RISK – ESTABLISHED ASSET'
    else:
        label = 'INSUFFICIENT DATA'
    
    # ---- Recommendation ----
    if score >= 7.0:
        rec = "AVOID"
    elif score >= 4.0:
        rec = "CAUTION"
    else:
        rec = "INCONCLUSIVE"
    
    risk_flags = [f for f in flags if not f.startswith(('Deep liquidity', 'Long track record', 'Widely distributed', 'LP locked for', 'Mint authority revoked', 'Freeze authority revoked', 'Owner holds only'))]
    # Prioritise the most dangerous flag: critically low holders > LP unlocked > others
    _priority_prefixes = ('Critically low', 'Zero liquidity', 'Very thin', 'Brand new', 'Very new', 'Mint authority live', 'Freeze authority live', 'CRITICAL concentration', 'Highly concentrated', 'LP unlocked', 'Floor:')
    priority_flags = [f for f in risk_flags if any(f.startswith(p) for p in _priority_prefixes)]
    main_risk = priority_flags[0] if priority_flags else (risk_flags[0] if risk_flags else (flags[0] if flags else 'New token — treat as high risk until proven otherwise'))

    return {
        "risk_score": round(score, 1), "risk_label": label, "code_summary": code_summary,
        "recommendation": rec, "flags": flags, "findings": [{"severity": "INFO", "title": f, "description": ""} for f in flags],
    }


# Placeholder handlers (implement as needed)
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome to Aegis! Use /help for commands.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/scan <address> – Scan a token address\n"
        "/wallet <address> – Scan all tokens in a wallet\n"
        "/subscribe – Get premium features\n"
        "/disclaimer – Legal info"
    )

async def disclaimer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "⚠️ <b>AEGIS DISCLAIMER</b>\n\n"
        "1. <b>Not Financial Advice:</b> Aegis is a <u>security scanner</u>, not a financial advisor. It does not provide buy/sell recommendations. Always conduct your own research (DYOR).\n\n"
        "2. <b>False Positives/Negatives:</b> No automated tool is 100% accurate. Aegis may occasionally flag safe tokens or miss sophisticated scams. Always do your own research (DYOR).\n"
        "3. <b>Liability:</b> The creators of Aegis are not responsible for any financial losses incurred while using the tool."
    )
    await update.message.reply_text(text, parse_mode="HTML")

async def grant_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != config["telegram"]["admin_user_id"]: return
    try:
        target_id = int(context.args[0])
        days = int(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /grant <user_id> <days>")
        return
    
    from core.subscription import get_or_create_user
    from datetime import timedelta
    db_user = await get_or_create_user(target_id)
    
    now = datetime.now(timezone.utc)
    if db_user.get("subscription_expires_at"):
        current_exp = datetime.fromisoformat(db_user["subscription_expires_at"])
        if current_exp < now: current_exp = now
    else:
        current_exp = now
        
    new_exp = current_exp + timedelta(days=days)
    
    import aiosqlite
    from core.db import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET subscription_expires_at = ? WHERE user_id = ?",
                       (new_exp.isoformat(), target_id))
        await db.commit()
    await update.message.reply_text(f"✅ Granted {days} days to {target_id}. Expires: {new_exp.strftime('%Y-%m-%d')}")

async def revoke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != config["telegram"]["admin_user_id"]: return
    try:
        target_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /revoke <user_id>")
        return
    
    import aiosqlite
    from core.db import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET subscription_expires_at = NULL WHERE user_id = ?", (target_id,))
        await db.commit()
    await update.message.reply_text(f"✅ Revoked subscription for {target_id}.")

# ─────────────────────────── Error handler ───────────────────────────
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)

# ─────────────────────────── Main ───────────────────────────────────
async def run_fastapi_server(port: int):
    """Run FastAPI as an asyncio task alongside the bot."""
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

async def main_async():
    """Initialize the bot and start FastAPI in the same event loop."""
    from core.db import init_db
    await init_db()

    try:
        from fetchers.etherscan import init_etherscan_pool
        etherscan_keys = config["explorers"].get("etherscan", [])
        if etherscan_keys:
            init_etherscan_pool(etherscan_keys, calls_per_second=5.0)
    except Exception as e:
        logger.warning(f"Etherscan pool init failed: {e}")

    # Create application
    application = Application.builder().token(config["telegram"]["bot_token"]).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("disclaimer", disclaimer_command))
    application.add_handler(CommandHandler("grant", grant_command))
    application.add_handler(CommandHandler("revoke", revoke_command))
    application.add_error_handler(error_handler)
    
    # Store application in FastAPI state
    app.state.application = application
    
    # Start polling
    await application.initialize()
    await application.start()
    
    # Run FastAPI
    port = config.get("fastapi_port", 8000)
    await run_fastapi_server(port)

def main():
    asyncio.run(main_async())

if __name__ == "__main__":
    main()
