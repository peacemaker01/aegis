#!/usr/bin/env python3
"""
Aegis Security Bot – Multi‑chain smart contract audit, smart money radar,
new token scout, deployer forensics, wallet portfolio analysis.
"""
import os, sys, logging, asyncio, json, html, io, time, httpx, threading
import uvicorn
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

from core.config import load_config, validate_config
from core.db import init_db
from core.session import run_scan
from core.deployer_session import run_deployer_analysis
from core.subscription import (
    get_or_create_user, can_use_service,
    process_verification, usage_logger, payment_verifier
)
from utils.validators import is_solana_address, is_evm_address
from services.smartmoney import get_smart_money_tokens
from services.newtokens import get_new_tokens
from services.pdf_report import generate_audit_pdf
from api import app
import uuid

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

config = load_config()
validation = validate_config(config)
for err in validation.get("errors", []):
    logger.error(err); sys.exit(1)
for warn in validation.get("warnings", []):
    logger.warning(warn)

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
    degen_lines.append(f"🎰 <b>{chain.upper()} RISK: {score}/10.0 \"{rec}\"</b>")

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
            lp_str = "LP: Burned ✅"
        elif lp_lock_days < 7:
            lp_str = f"LP: Locked {lp_lock_days}d ⚠️ UNLOCKS SOON"
        else:
            lp_str = f"LP: Locked {lp_lock_days}d"
    else:
        lp_str = "LP: Unknown ⚠️"

    if top10_pct and top10_pct > 50:
        degen_lines.append(f"💰 <b>BAG ALERT:</b> Top 10 holders own {top10_pct:.0f}% – {lp_str}")
    else:
        degen_lines.append(f"💰 <b>TOKENOMICS:</b> {lp_str}")

    # Age and liquidity
    age_str = "?"
    solsniffer = raw.get('solsniffer', {})
    if solsniffer.get('deploy_time'):
        try:
            deploy_dt = datetime.fromisoformat(solsniffer['deploy_time'].replace('Z', '+00:00'))
            age_seconds = (datetime.now(timezone.utc) - deploy_dt).total_seconds()
            age_days = max(0, int(age_seconds / 86400))
            if age_days == 0:
                age_hours = max(0, int(age_seconds / 3600))
                age_str = f"{age_hours}hrs" if age_hours > 0 else "minutes"
            else:
                age_str = f"{age_days}d"
        except Exception:
            age_str = "?"
    
    liq = (raw.get('solsniffer', {}).get('liquidity_list', [{}])[0].get('pumpswap', {}).get('amount', 0)
           or raw.get('rugcheck', {}).get('lp_locked_pct', 0))
    liq_str = f"${liq:,.0f}" if liq else "$0 ⚠️"
    degen_lines.append(f"⏱️ <b>AGE:</b> {age_str} | <b>LIQ:</b> {liq_str}")

    # Main risk
    main_risk = result.get('summary', 'Unknown risk')
    degen_lines.append(f"🚨 <b>MAIN RISK:</b> {escape_html(main_risk)[:200]}")

    # Context
    is_pumpfun = result.get('flags', {}).get('is_pumpfun', False)
    if is_pumpfun or chain == 'solana':
        degen_lines.append(f"⚠️ <b>CONTEXT:</b> 99% of these die. Score is relative to other rugs.")

    if result.get("cross_chain_alert"):
        degen_lines.append(f"🚨 <b>⚠️ CROSS-CHAIN ALERT</b>\n{escape_html(result.get('cross_chain_summary',''))}")

    return "\n\n".join(degen_lines)

# ─────────────────────────── Command handlers ───────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_private_chat(update): return
    user = update.effective_user
    db_user = await get_or_create_user(user.id, user.username or "", user.first_name or "")
    allowed, reason = can_use_service(db_user)

    if reason == "trial":
        trial_end = datetime.fromisoformat(db_user["trial_ends_at"]).strftime("%B %d, %Y")
        status = f"🆓 Trial active — expires {trial_end}"
        status_color = "🟢"
    elif reason == "subscribed":
        exp = datetime.fromisoformat(db_user["subscription_expires_at"]).strftime("%B %d, %Y")
        status = f"💎 Premium member — next renewal {exp}"
        status_color = "💠"
    else:
        status = "⏳ Trial expired — tap Subscribe to unlock"
        status_color = "🔴"

    name = escape_html(user.first_name or 'trader')
    text = (
        f"🛡️ <b>AEGIS PREMIUM DASHBOARD</b>\n"
        f"<i>Your multi‑chain security edge</i>\n\n"
        f"📈 <b>Market Pulse:</b> Solana & EVM Trending\n"
        f"🧠 <b>Entity Intelligence:</b> Deployer Forensics\n\n"
        f"🛡️ <b>AEGIS VERIFIED:</b> {status_color} {escape_html(status)}\n\n"
        f"Welcome back, {name}. System status: NOMINAL."
    )
    keyboard = [
        [InlineKeyboardButton("🔍 Scan Contract", callback_data="cmd_scan_prompt"),
         InlineKeyboardButton("⚠️ DEGEN FLOW", callback_data="cmd_degenflow")],
        [InlineKeyboardButton("🕵️ Deployer Check", callback_data="cmd_deployer_prompt")],
        [InlineKeyboardButton("🤝 Trust Center", callback_data="cmd_trust"),
         InlineKeyboardButton("📊 Compare", callback_data="cmd_compare")],
        [InlineKeyboardButton("📋 Help Center", callback_data="cmd_help"),
         InlineKeyboardButton("💳 Status", callback_data="cmd_status")],
    ]
    if reason != "subscribed" and reason != "trial":
        keyboard.insert(3, [InlineKeyboardButton("💎 Subscribe $14.99/mo", callback_data="cmd_subscribe")])
        
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def help_command(update, context):
    if not await _require_private_chat(update): return
    await start(update, context)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_private_chat(update): return
    user_id = update.effective_user.id
    db_user = await get_or_create_user(user_id)
    allowed, reason = can_use_service(db_user)
    if reason == "trial":
        ends = datetime.fromisoformat(db_user["trial_ends_at"]).strftime("%Y-%m-%d %H:%M UTC")
        msg = f"<b>🆓 Free Trial</b>\nEnds: {escape_html(ends)}"
    elif reason == "subscribed":
        ends = datetime.fromisoformat(db_user["subscription_expires_at"]).strftime("%Y-%m-%d %H:%M UTC")
        wallet = db_user.get("wallet_address", "Not set")
        msg = f"<b>✅ Premium Active</b>\nExpires: {escape_html(ends)}\nWallet: <code>{escape_html(wallet)}</code>"
    else:
        msg = "<b>⛔ No active subscription</b>\nUse /subscribe to purchase."
    await update.message.reply_text(msg, parse_mode="HTML")

async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_private_chat(update): return
    user_id = update.effective_user.id
    db_user = await get_or_create_user(user_id)
    if can_use_service(db_user)[0] and db_user.get("subscription_expires_at"):
        exp = datetime.fromisoformat(db_user["subscription_expires_at"]).strftime("%Y-%m-%d")
        await update.message.reply_text(f"✅ You already have an active subscription until {exp}.")
        return

    keyboard = [
        [InlineKeyboardButton("1 Month ($79)", callback_data="cryptomus_pay_monthly")],
        [InlineKeyboardButton("1 Year ($699)", callback_data="cryptomus_pay_yearly")]
    ]
    msg = (
        f"<b>💎 Subscribe to Aegis Premium</b>\n\n"
        f"Unlock unlimited deep scans, deployer forensics, wallet tracking, and the Pump.fun radar.\n\n"
        f"Select a subscription tier to generate a secure checkout link (supports Crypto via Cryptomus):"
    )
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

async def trust_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The Aegis Trust Manifesto."""
    text = (
        "🛡️ <b>THE AEGIS TRUST MANIFESTO</b>\n\n"
        "1️⃣ <b>Objective Reality:</b> We don't believe in 'Safe' lists. We believe in data. Every token starts at 0.0 risk and earns its score through verifiable on‑chain history.\n\n"
        "2️⃣ <b>Transparency:</b> Every point in an Aegis Score is explained. If a token is risky, we tell you exactly why (unlocked LP, live authorities, or concentrated supply).\n\n"
        "3️⃣ <b>Neutrality:</b> We are not a marketing platform. We are a security filter. Our goal is to be the 'Casino Bouncer' for your portfolio.\n\n"
        "<i>AEGIS VERIFIED — Data. Not Hype.</i>"
    )
    if update.message:
        await update.message.reply_text(text, parse_mode="HTML")
    else:
        await update.callback_query.message.edit_text(text, parse_mode="HTML")

async def compare_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """How Aegis compares to others."""
    text = (
        "📊 <b>AEGIS VS. THE MARKET</b>\n\n"
        "🔹 <b>Traditional Bots:</b> Hostile defaults, origin-biased, often pay-to-play 'safe' lists.\n"
        "🔸 <b>Aegis SaaS:</b> Objective additive scoring, chain-agnostic, zero-bias audits.\n\n"
        "We prioritize <b>Tokenomics</b> (Bag Alerts) and <b>Authority Status</b> because code can be obfuscated, but on‑chain distribution and control never lie."
    )
    if update.message:
        await update.message.reply_text(text, parse_mode="HTML")
    else:
        await update.callback_query.message.edit_text(text, parse_mode="HTML")

# ───────────────────── Degen Flow Objective Scoring ──────────────────────

async def calculate_degen_risk_solana(raw: dict, ca: str, fast_mode: bool) -> dict:
    """Objective Solana scoring – additive model starting from 0."""
    rugcheck   = raw.get('rugcheck', {}) or {}
    mint_info  = raw.get('mint_info', {}) or {}
    holders    = raw.get('holders', [])

    # ── Fetch liquidity + age from DexScreener ─────────────────────────
    liq = 0.0
    age_hours = 999
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{ca}")
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get('pairs', [])
                if pairs:
                    best = max(pairs, key=lambda p: float(p.get('liquidity', {}).get('usd', 0) or 0))
                    liq = float(best.get('liquidity', {}).get('usd', 0) or 0)
                    created_at = best.get('pairCreatedAt') or 0
                    if created_at:
                        age_hours = (datetime.now(timezone.utc).timestamp() - created_at / 1000) / 3600
    except Exception:
        pass

    # ── Authorities from on‑chain (Helius) ─────────────────────────────
    mint_revoked = mint_info.get('mint_authority') is None
    freeze_revoked = mint_info.get('freeze_authority') is None

    # ── Holder concentration (if available) ────────────────────────────
    top10_pct = None
    # RugCheck returns top-20 holders list — always 20 entries.
    # Use totalHolders from the report for the real count; fall back to len() only if missing.
    holder_count = int(rugcheck.get('totalHolders', 0)) or len(holders)
    if holders:
        top10_pct = min(100.0, sum(h.get('percentage', 0) for h in holders[:10]))

    # ── LP lock from RugCheck ─────────────────────────────────────────
    lp_locked = False
    lp_lock_days = 0
    locks = rugcheck.get('locks', [])
    if locks:
        max_lock = max(locks, key=lambda x: x.get('unlockDate', 0))
        unlock_ts = (max_lock.get('unlockDate') or 0) / 1000
        if unlock_ts > datetime.now(timezone.utc).timestamp():
            lp_locked = True
            lp_lock_days = int((unlock_ts - datetime.now(timezone.utc).timestamp()) / 86400)

    # ── Additive scoring – start from 0 ───────────────────────────────
    score = 0.0
    flags = []

    # Pump.fun base‑rate adjustment
    is_pump = ca.endswith('pump')
    if is_pump:
        score += 2.0

    # ---- Liquidity ----
    if liq == 0:
        score += 10.0
        flags.append('Zero liquidity – cannot sell')
    elif liq < 10_000:
        score += 3.0
        flags.append(f'Very thin liquidity (${liq:,.0f})')
    elif liq < 100_000:
        score += 1.0
        flags.append(f'Low liquidity (${liq:,.0f})')
    elif liq > 1_000_000:
        score -= 1.0
        flags.append(f'Deep liquidity (${liq:,.0f})')

    # ---- Age (STRICT GUARD) ----
    if age_hours is not None and age_hours != 999:
        if age_hours < 1:
            score += 2.0
            flags.append('Brand new (<1 hour)')
        elif age_hours <= 24:          # FIX: was < 24, misses tokens at exactly 24h
            score += 1.5
            flags.append(f"Very new ({int(age_hours)}h)")
        elif age_hours < 720:
            score += 0.5
            flags.append(f'Less than 30 days old')
        elif age_hours > 4320:
            score -= 1.0
            flags.append('Long track record (180+ days)')

    # ---- Holder concentration (only if data available) ----
    if top10_pct is not None:
        if top10_pct > 80:
            score += 4.0
            flags.append(f'CRITICAL concentration: Top10 hold {top10_pct:.0f}%')
        elif top10_pct > 70:
            score += 3.0
            flags.append(f'Highly concentrated: Top10 hold {top10_pct:.0f}%')
        elif top10_pct > 50:
            score += 1.0
            flags.append(f'Concentrated: Top10 hold {top10_pct:.0f}%')
        elif top10_pct > 30:
            score += 0.5
            flags.append(f'Elevated concentration: Top10 hold {top10_pct:.0f}%')
        elif top10_pct < 30:
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
        score += 3.0
        flags.append('LP unlocked – liquidity can be removed')
    elif lp_lock_days > 180:
        score -= 1.0
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

    # Rule 3: Extreme supply concentration (Top 10 > 80%) → minimum 7.0 (HIGH RISK)
    if top10_pct is not None and top10_pct > 80:
        if score < 7.0:
            score = 7.0
            flags.append('Floor: Extreme supply concentration — minimum HIGH RISK')

    # Rule 4: High supply concentration (Top 10 > 50%) → minimum 5.0 (ELEVATED RISK)
    elif top10_pct is not None and top10_pct > 50:
        if score < 5.0:
            score = 5.0
            flags.append('Floor: High supply concentration — minimum ELEVATED RISK')

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
        label = 'LOW RISK – VERIFIED BY ON‑CHAIN DATA'

    # ---- Build summaries ----
    code_parts = ['Pump.fun' if is_pump else 'SPL Token']
    code_parts.append('Mint: Revoked' if mint_revoked else 'Mint: LIVE')
    code_parts.append('Freeze: Revoked' if freeze_revoked else 'Freeze: LIVE')
    code_summary = ' | '.join(code_parts)

    if holders:
        bag_alert = f'Top10: {top10_pct:.0f}% | Holders: {holder_count}'
    else:
        bag_alert = 'Holder data unavailable'
    if liq:
        bag_alert += f' | LIQ: ${liq:,.0f}'

    lp_status = f'Locked {lp_lock_days}d' if lp_locked else 'Unlocked'
    if age_hours is not None and age_hours != 999:
        age_str = f'{int(age_hours)}h' if age_hours < 72 else f'{int(age_hours / 24)}d'
    else:
        age_str = '?'

    risk_flags = [f for f in flags if not f.startswith(('Deep liquidity', 'Long track record', 'Widely distributed', 'LP locked for', 'Mint authority revoked', 'Freeze authority revoked', 'Owner holds only'))]
    _priority_prefixes = ('Critically low', 'Zero liquidity', 'Very thin', 'Brand new', 'Very new', 'Mint authority live', 'Freeze authority live', 'CRITICAL concentration', 'Highly concentrated', 'LP unlocked', 'Floor:')
    priority_flags = [f for f in risk_flags if any(f.startswith(p) for p in _priority_prefixes)]
    main_risk = priority_flags[0] if priority_flags else (risk_flags[0] if risk_flags else (flags[0] if flags else 'Insufficient data – see flags below'))

    return {
        'score': round(score, 1),
        'label': label,
        'code_summary': code_summary,
        'bag_alert': bag_alert,
        'age_str': age_str,
        'liq_str': f'${liq:,.0f}' if liq else '$0',
        'lp_status': lp_status,
        'main_risk': main_risk,
        'recommendation': '',
        'flags': flags
    }


async def calculate_degen_risk_evm(raw: dict, ca: str, fast_mode: bool) -> dict:
    """Objective EVM scoring – additive model starting from 0."""
    goplus = raw.get('goplus', {}) or {}
    static = raw.get('static', {}) or {}
    slither_findings = raw.get('slither', [])
    clone_result = raw.get('clone', {})

    # ── DexScreener (liq, age, name) ──────────────────────────────────
    liq = 0.0
    age_hours = 999
    dex_token_name = None
    dex_token_symbol = None
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{ca}")
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get('pairs', [])
                if pairs:
                    best = max(pairs, key=lambda p: float(p.get('liquidity', {}).get('usd', 0) or 0))
                    base = best.get('baseToken', {})
                    dex_token_name = base.get('name')
                    dex_token_symbol = base.get('symbol')
                    liq = float(best.get('liquidity', {}).get('usd', 0) or 0)
                    created_at = best.get('pairCreatedAt', 0)
                    if created_at:
                        age_hours = (datetime.now(timezone.utc).timestamp() - created_at / 1000) / 3600
    except Exception:
        pass

    # ── Etherscan fallback for age ─────────────────────────────────────
    if age_hours == 999:
        tx_hash = raw.get('creation', {}).get('tx_hash') or raw.get('tx_hash')
        if tx_hash:
            try:
                api_key = config["explorers"]["etherscan"][0]
                async with httpx.AsyncClient(timeout=6.0) as client:
                    resp = await client.get(
                        f"https://api.etherscan.io/v2/api?chainid={raw.get('chain_id', 1)}"
                        f"&module=transaction&action=gettxinfo&txhash={tx_hash}&apikey={api_key}"
                    )
                    if resp.status_code == 200:
                        tx_data = resp.json()
                        if tx_data.get("status") == "1":
                            timestamp = int(tx_data["result"]["timeStamp"])
                            age_hours = (datetime.now(timezone.utc).timestamp() - timestamp) / 3600
            except Exception:
                pass

    # ── GoPlus signals ─────────────────────────────────────────────────
    honeypot = goplus.get('gp_is_honeypot', False)
    mintable = goplus.get('gp_is_mintable', False) if goplus.get('gp_is_mintable') is not None else None
    transfer_pausable = goplus.get('gp_transfer_pausable', False) if goplus.get('gp_transfer_pausable') is not None else None
    owner_pct = float(goplus.get('gp_owner_percent')) if goplus.get('gp_owner_percent') is not None else None
    lp_locked = not goplus.get('gp_cannot_sell_all', False)
    
    # Extract total holders count
    holder_count = 0
    if goplus.get('gp_holder_count'):
        try:
            holder_count = int(goplus.get('gp_holder_count', 0))
        except ValueError:
            pass

    # Extract top 10 holders percentage
    top10_pct = None
    gp_holders = goplus.get('holders', [])
    if gp_holders:
        try:
            top10_pct = min(100.0, sum(float(h.get('percent', 0) or 0) * 100 for h in gp_holders[:10]))
        except Exception:
            pass

    if not goplus.get('goplus_available'):
        owner_pct = None
        lp_locked = None
        transfer_pausable = None
        top10_pct = None
        holder_count = 0

    # ── Additive scoring ───────────────────────────────────────────────
    score = 0.0
    flags = []

    # Honeypot → immediate 10
    if honeypot:
        return make_risk_dict(10.0, 'CONFIRMED HONEYPOT', 'GoPlus: Honeypot detected',
                              'Cannot sell – Honeypot confirmed', '?', '$?', 'None',
                              'Honeypot contract. You cannot sell.', '',
                              ['GoPlus: is_honeypot = true'], dex_token_name, dex_token_symbol)

    # ---- Liquidity ----
    if liq == 0:
        score += 10.0
        flags.append('Zero liquidity – cannot sell')
    elif liq < 10_000:
        score += 3.0
        flags.append(f'Very thin liquidity (${liq:,.0f})')
    elif liq < 100_000:
        score += 1.0
        flags.append(f'Low liquidity (${liq:,.0f})')
    elif liq > 1_000_000:
        score -= 1.0
        flags.append(f'Deep liquidity (${liq:,.0f})')

    # ---- Age (STRICT: only apply if we actually have the data) ----
    if age_hours is not None and age_hours != 999:
        if age_hours < 1:
            score += 2.0
            flags.append('Brand new (<1 hour)')
        elif age_hours <= 24:
            score += 1.5
            flags.append(f"Very new ({int(age_hours)}h)")
        elif age_hours < 720:
            score += 0.5
            flags.append(f'Less than 30 days old')
        elif age_hours > 4320:
            score -= 1.0
            flags.append('Long track record (180+ days)')

    # ---- Holder concentration (only if data available) ----
    if top10_pct is not None:
        if top10_pct > 80:
            score += 4.0
            flags.append(f'CRITICAL concentration: Top10 hold {top10_pct:.0f}%')
        elif top10_pct > 70:
            score += 3.0
            flags.append(f'Highly concentrated: Top10 hold {top10_pct:.0f}%')
        elif top10_pct > 50:
            score += 1.0
            flags.append(f'Concentrated: Top10 hold {top10_pct:.0f}%')
        elif top10_pct > 30:
            score += 0.5
            flags.append(f'Elevated concentration: Top10 hold {top10_pct:.0f}%')
        elif top10_pct < 30:
            score -= 1.0
            flags.append(f'Widely distributed: Top10 hold {top10_pct:.0f}%')

    # Extra non-overlapping flags for owner percentage if notable
    if owner_pct is not None and owner_pct > 20:
        flags.append(f'Owner holds {owner_pct:.0f}%')
    elif owner_pct is not None and owner_pct <= 20:
        flags.append(f'Owner holds only {owner_pct:.0f}%')

    # ---- Holder count (absolute) ----
    if 0 < holder_count < 20:
        score += 2.0
        flags.append(f'Critically low holder count: only {holder_count} holders')
    elif 0 < holder_count < 50:
        score += 1.5
        flags.append(f'Low holder count: {holder_count} holders')

    # ---- LP lock ----
    if lp_locked is not None:
        if not lp_locked:
            score += 3.0
            flags.append('LP unlocked – liquidity can be removed')

    # ---- Authorities & Technical Checks ----
    if mintable is True:
        score += 2.0
        flags.append('Mint function exists – supply can be inflated')
    elif mintable is False:
        score -= 0.25
        flags.append('Mint authority revoked')

    if transfer_pausable is True:
        score += 2.0
        flags.append('Transfer can be paused – tokens can be frozen')
    elif transfer_pausable is False:
        score -= 0.25
        flags.append('Freeze authority revoked')

    # ---- Slither ----
    actual_slither = [f for f in slither_findings if isinstance(f, dict) and not f.get('_slither_metadata')]
    high_count = len([f for f in actual_slither if f.get('severity') == 'HIGH'])
    if high_count == 1:
        score += 2.0
        flags.append(f'Slither found 1 HIGH severity issue')
    elif high_count == 2:
        score += 3.5
        flags.append(f'Slither found 2 HIGH severity issues')
    elif high_count >= 3:
        score += 5.0
        flags.append(f'Slither found {high_count} HIGH severity issues')

    # Clone detection
    clone_score = clone_result.get('similarity_score', 0)
    if clone_score > 0.6:
        score += 2.0
        flags.append(f'Bytecode similarity to rugs: {clone_score:.0%}')

    # Proxy detection penalty
    if goplus.get('gp_is_proxy') is True:
        score += 2.0
        flags.append('Proxy contract detected — upgradeability risk')

    # ---- Floor Rules ----
    # Rule 1: New token (≤48h) with unlocked LP cannot score below 5.0
    if age_hours is not None and age_hours != 999 and age_hours <= 48 and lp_locked is False:
        if score < 5.0:
            score = 5.0
            flags.append('Floor: New token with unlocked LP — minimum HIGH RISK')

    # Rule 2: LP unlocked + top10 >50% → minimum 4.0
    if lp_locked is False and top10_pct is not None and top10_pct > 50:
        if score < 4.0:
            score = 4.0
            flags.append('Structural floor: Unlocked LP + concentrated holders')

    # Rule 3: Extreme supply concentration (Top 10 > 80%) → minimum 7.0 (HIGH RISK)
    if top10_pct is not None and top10_pct > 80:
        if score < 7.0:
            score = 7.0
            flags.append('Floor: Extreme supply concentration — minimum HIGH RISK')

    # Rule 4: High supply concentration (Top 10 > 50%) → minimum 5.0 (ELEVATED RISK)
    elif top10_pct is not None and top10_pct > 50:
        if score < 5.0:
            score = 5.0
            flags.append('Floor: High supply concentration — minimum ELEVATED RISK')

    # Rule 5: Mintable function exists → minimum 5.0 (ELEVATED RISK)
    if mintable is True:
        if score < 5.0:
            score = 5.0
            flags.append('Floor: Mintable token — minimum ELEVATED RISK')

    # Rule 6: High severity code vulnerability → minimum 5.0
    if high_count >= 1:
        if score < 5.0:
            score = 5.0
            flags.append('Floor: High-severity code vulnerability detected')

    # Final clamping
    score = max(0.0, min(10.0, score))

    # ---- Structural risk count ----
    _structural_risks = sum([
        lp_locked is False,
        (top10_pct or 0) > 50,
        (age_hours or 999) <= 48,
        (0 < holder_count < 50),
    ])

    # Label
    if liq == 0:
        label = 'INSTANT RUG – UNSWAPPABLE'
    elif score >= 9.0:
        label = 'EXTREME RISK – LIKELY RUG'
    elif score >= 7.0:
        label = 'HIGH RISK – DEGEN GAMBLE'
    elif score >= 5.0:
        label = 'ELEVATED RISK – SPECULATIVE'
    elif _structural_risks >= 2:
        label = 'HIGH RISK – UNVERIFIED'
    elif score >= 2.5:
        label = 'MODERATE RISK – PROCEED WITH CARE'
    elif score >= 1.0:
        label = 'LOW RISK – ESTABLISHED ASSET'
    else:
        label = 'LOW RISK – VERIFIED BY ON‑CHAIN DATA'

    # Summaries
    code_parts = ['Proxy/Clone' if goplus.get('gp_is_proxy') else 'Standard Token']
    code_parts.append('Mint: Revoked' if mintable is False else 'Mint: LIVE' if mintable is True else 'Mint: Unknown')
    code_parts.append('Freeze: Revoked' if transfer_pausable is False else 'Freeze: LIVE' if transfer_pausable is True else 'Freeze: Unknown')
    code_summary = ' | '.join(code_parts)

    if top10_pct is not None:
        bag_alert = f'Top10: {top10_pct:.0f}% | Holders: {holder_count}'
    else:
        bag_alert = f'Owner: {owner_pct:.0f}% | Holders: {holder_count}' if owner_pct is not None else 'Holder data unavailable'
    if liq:
        bag_alert += f' | LIQ: ${liq:,.0f}'

    lp_status_str = 'Locked' if lp_locked else 'Unlocked' if lp_locked is not None else 'Unknown'
    # age_hours == 999 is the sentinel for unknown – never show it as a real age
    if age_hours is not None and age_hours != 999:
        age_str = f'{int(age_hours)}h' if age_hours < 72 else f'{int(age_hours / 24)}d'
    else:
        age_str = '?'

    risk_flags = [f for f in flags if not f.startswith(('Deep liquidity', 'Long track record', 'Widely distributed', 'LP locked for', 'Mint authority revoked', 'Freeze authority revoked', 'Owner holds only'))]
    _priority_prefixes = ('Critically low', 'Zero liquidity', 'Very thin', 'Brand new', 'Very new', 'Mint authority live', 'Freeze authority live', 'CRITICAL concentration', 'Highly concentrated', 'LP unlocked', 'Floor:', 'Mint function exists', 'Slither found', 'Bytecode similarity')
    priority_flags = [f for f in risk_flags if any(f.startswith(p) for p in _priority_prefixes)]
    main_risk = priority_flags[0] if priority_flags else (risk_flags[0] if risk_flags else 'No significant on-chain risks identified')

    return make_risk_dict(round(score, 1), label, code_summary, bag_alert,
                          age_str, f'${liq:,.0f}' if liq else '$0', lp_status_str,
                          main_risk,
                          '', flags, dex_token_name, dex_token_symbol)
def format_age(hours: float) -> str:
    if hours is None or hours == 999:
        return "?"
    if hours < 72:
        return f"{int(hours)}h"
    return f"{int(hours/24)}d"


def make_risk_dict(score, label, code_summary, bag_alert, age_str, liq_str, lp_status, main_risk, recommendation, flags, dex_name=None, dex_symbol=None):
    return {
        'score': score,
        'label': label,
        'code_summary': code_summary,
        'bag_alert': bag_alert,
        'age_str': age_str,
        'liq_str': liq_str,
        'lp_status': lp_status,
        'main_risk': main_risk,
        'recommendation': recommendation,
        'flags': flags,
        'dex_name': dex_name,
        'dex_symbol': dex_symbol,
    }

# ─────────────────────── Banned‑word guard ───────────────────────────────

BANNED_WORDS = ['Safe', 'Medium Risk',
                'Audited', 'Secure', 'Gem', 'Alpha', 'Smart Money']

def sanitize_output(text: str) -> None:
    """Raise ValueError if any banned word appears in the text."""
    for word in BANNED_WORDS:
        if word.lower() in text.lower():
            raise ValueError(f"BANNED WORD DETECTED: {word}")

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE, fast_mode: bool = True):
    if not await _require_private_chat(update): return
    if not await require_subscription(update, context): return
    args = context.args or []
    query = update.callback_query
    if not args:
        usage = "Usage: /scan &lt;address&gt; [chain]\nChain is auto‑detected for Solana (base58) and EVM (0x)."
        if query: await query.message.edit_text(usage, parse_mode="HTML")
        else: await update.message.reply_text(usage, parse_mode="HTML")
        return
    address = args[0]
    chain = args[1] if len(args) > 1 else ("solana" if is_solana_address(address) else config["preferences"]["default_chain"] if is_evm_address(address) else None)
    if not chain:
        fail_msg = "❌ Unknown address format."
        if query: await query.message.edit_text(fail_msg, parse_mode="HTML")
        else: await update.message.reply_text(fail_msg, parse_mode="HTML")
        return
    if (chain == "solana" and not is_solana_address(address)) or (chain != "solana" and not is_evm_address(address)):
        fail_msg = "❌ Address format doesn't match chain."
        if query: await query.message.edit_text(fail_msg, parse_mode="HTML")
        else: await update.message.reply_text(fail_msg, parse_mode="HTML")
        return
    user_id = update.effective_user.id
    await usage_logger(user_id, "scan" if fast_mode else "deepscan", address, chain)
    mode_str = "deep" if not fast_mode else "security"
    chain_emoji = "🔷" if chain == "solana" else "⛓️"
    init_msg = f"{chain_emoji} Starting {mode_str} scan on <code>{escape_html(address)}</code> ({chain.upper()})...\n<i>Initializing...</i>"
    if query: msg = await query.message.edit_text(init_msg, parse_mode="HTML")
    else: msg = await update.message.reply_text(init_msg, parse_mode="HTML")
    async def progress_callback(stage: str):
        try: await msg.edit_text(f"{chain_emoji} {mode_str.capitalize()} scan on <code>{escape_html(address)}</code> ({chain.upper()})...\n<i>{escape_html(stage)}</i>", parse_mode="HTML")
        except Exception: pass
    async with SCAN_SEMAPHORE:
        try:
            contract, result = await run_scan(
                address, chain, config, stream=False, debug=DEBUG,
                fast_mode=fast_mode, progress_callback=progress_callback
            )
        except Exception as e:
            logger.error(f"Scan error: {e}")
            await msg.edit_text(f"❌ Error: {escape_html(str(e))}", parse_mode="HTML")
            return

    # ── Deterministic Degen Flow scoring ─────────────────────────────────
    raw = result.get('_raw', {})
    if chain == "solana":
        risk = await calculate_degen_risk_solana(raw, address, fast_mode)
    else:
        risk = await calculate_degen_risk_evm(raw, address, fast_mode)

    # ── Synchronise result dictionary with deterministic metrics for PDF consistency ──
    result['risk_score'] = risk['score']
    result['recommendation'] = risk['label']
    
    # Generate executive summary for PDF
    summary_lines = [
        f"Aegis Automated Security Assessment for {display_name if 'display_name' in locals() else address[:10]}.",
        "",
        f"🎰 RISK ASSESSMENT: {risk['score']:.1f}/10.0 (VERDICT: {risk['label']})",
        f"🚨 MAIN RISK: {risk['main_risk']}"
    ]
    if risk.get('flags'):
        summary_lines.append("")
        summary_lines.append("SECURITY FLAGS & FINDINGS:")
        for f in risk['flags']:
            summary_lines.append(f"- {f}")
    result['summary'] = "\n".join(summary_lines)

    # Set exact security flag overrides for PDF
    if chain == "solana":
        mint_info = raw.get('mint_info', {}) or {}
        mint_revoked = mint_info.get('mint_authority') is None
        freeze_revoked = mint_info.get('freeze_authority') is None
        
        result['honeypot'] = 'Zero liquidity' in risk['main_risk'] or 'Honeypot' in risk['main_risk']
        result['mint_function'] = not mint_revoked
        result['owner_renounced'] = mint_revoked
        result['hidden_owner'] = False
        result['blacklist_function'] = not freeze_revoked
        result['transfer_tax_modifiable'] = False
        result['proxy_pattern'] = False
        result['liquidity_concerns'] = (raw.get('dex', {}).get('liquidity', 0) or 0) < 50000
    else:
        goplus = raw.get('goplus', {}) or {}
        owner_addr = goplus.get('gp_owner_address', '')
        is_renounced = owner_addr in ('', '0x0000000000000000000000000000000000000000', '0x000000000000000000000000000000000000dead')
        
        result['honeypot'] = goplus.get('gp_is_honeypot', False)
        result['mint_function'] = goplus.get('gp_is_mintable', False)
        result['owner_renounced'] = is_renounced
        result['hidden_owner'] = goplus.get('gp_hidden_owner', False)
        result['blacklist_function'] = goplus.get('gp_is_blacklisted', False) or goplus.get('gp_transfer_pausable', False)
        result['transfer_tax_modifiable'] = goplus.get('gp_slippage_modifiable', False)
        result['proxy_pattern'] = goplus.get('gp_is_proxy', False)
        
        # Parse liquidity from risk['liq_str']
        liq_val = 0.0
        if risk.get('liq_str') and '$' in risk['liq_str']:
            try:
                liq_val = float(risk['liq_str'].replace('$','').replace(',',''))
            except Exception:
                pass
        result['liquidity_concerns'] = liq_val > 0 and liq_val < 50000

    # ── Build DEGEN output ─────────────────────────────────────────────
    # Token name from contract data (fallback to address)
    evm_token_name = contract.get('token_name') or risk.get('dex_name') or address[:12] + '…'
    evm_token_symbol = contract.get('token_symbol') or risk.get('dex_symbol') or '???'
    token_name = escape_html(evm_token_name)
    token_symbol = escape_html(evm_token_symbol)

    age_str = risk.get('age_str', '?')

    deployer = None
    if chain == "solana":
        deployer = contract.get("deployer_address")
    else:
        deployer = contract.get("creator") or result.get("_raw", {}).get("goplus", {}).get("gp_creator_address")

    lines = [
        f"<b>{token_name} ({token_symbol})</b>",
        f"<code>{escape_html(address)}</code>",
    ]
    if deployer:
        lines.append(f"🕵️ <b>DEPLOYER:</b> <code>{escape_html(deployer)}</code>")
    
    lines.extend([
        "",
        f"🎰 <b>{chain.upper()} RISK: {risk['score']:.1f}/10.0 \"{risk['label']}\"</b>",
        f"💰 <b>BAG ALERT:</b> {risk['bag_alert']}",
        f"🔍 <b>CODE:</b> {risk['code_summary']}",
        f"⏱️ <b>AGE:</b> {age_str} | <b>LIQ:</b> {risk['liq_str']} | <b>LP:</b> {risk['lp_status']}",
        f"🚨 <b>MAIN RISK:</b> {risk['main_risk']}",
    ])

    # Append deep‑specific flags if present
    if risk.get('flags'):
        lines.append("")
        lines.append("<b>DETAILED FLAGS:</b>")
        for f in risk['flags'][:10]:
            lines.append(f"• {f}")

    lines.append(f"Scan: {'Deep' if not fast_mode else 'Fast'} | {datetime.now(timezone.utc).strftime('%H:%M UTC')}")

    reply = "\n".join(lines)

    # ── Sanitise – no banned words ──────────────────────────────────────
    sanitize_output(reply)

    keyboard = [[InlineKeyboardButton("📄 Download PDF Report", callback_data=f"pdf_{address}")]]
    if deployer:
        keyboard.append([InlineKeyboardButton("🕵️ Analyze Deployer Wallet", callback_data=f"deployer_check_{deployer}")])
        
    await msg.edit_text(reply, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    context.user_data.update({
        'last_scan_raw': raw,
        'last_scan_chain': chain,
        'last_scan_result': result,
        'last_scan_contract': contract,
    })

async def deepscan_command(update, context):
    if not await _require_private_chat(update): return
    if not await require_subscription(update, context): return
    await scan_command(update, context, fast_mode=False)

async def deployer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_private_chat(update): return
    if not await require_subscription(update, context): return
    args = context.args
    if not args: await update.effective_message.reply_text("Usage: /deployer &lt;address>"); return
    address = args[0]; user_id = update.effective_user.id; await usage_logger(user_id, "deployer", address)
    force_refresh = any(arg.lower() in ["force", "refresh", "--force"] for arg in args[1:])
    from utils.validators import is_solana_address
    is_sol = is_solana_address(address)
    loading_text = f"🔍 Analyzing deployer <code>{escape_html(address)}</code> on Solana..." if is_sol else f"🔍 Analyzing deployer <code>{escape_html(address)}</code> across 5 chains..."
    msg = await update.effective_message.reply_text(loading_text, parse_mode="HTML")
    try:
        profile, result = await run_deployer_analysis(address, config, chains=["eth","bsc","polygon","base","arb"], stream=False, debug=DEBUG, force_refresh=force_refresh)
        score = result.get('reputation_score', result.get('risk_score','N/A')); verdict = result.get('verdict','N/A'); rec = result.get('recommendation','N/A')
        summary = result.get('summary','No summary.'); total = profile.get('total_deployments',0)
        
        reply = (
            f"<b>🕵️ Deployer Forensics</b>\nWallet: <code>{escape_html(address)}</code>\nContracts deployed: {total}\n\n"
            f"Reputation Score: <b>{score}/100</b>\nVerdict: <b>{escape_html(verdict)}</b>\nRecommendation: <b>{escape_html(rec)}</b>\n\n"
            f"📝 <b>Summary:</b>\n{escape_html(summary)}"
        )
        
        # Enrich with Nansen data if available
        nansen = profile.get("nansen") or {}
        nansen_label = nansen.get("label") or {}
        nansen_rep = nansen.get("reputation") or {}
        
        nansen_summary = ""
        if nansen_label or nansen_rep:
            is_goplus = nansen_label.get("entity") == "goplus_fallback"
            if is_goplus:
                nansen_summary = "\n\n🛡️ <b>GoPlus Security Insights (Free Alternative)</b>"
            else:
                nansen_summary = "\n\n🏛️ <b>Nansen Institutional Insights</b>"

            if nansen_label.get("label"):
                label_ent = "security_audit" if is_goplus else nansen_label.get('entity', 'unknown')
                nansen_summary += f"\n• Label: <code>{escape_html(nansen_label['label'])}</code> ({escape_html(label_ent)})"
                if nansen_label.get("is_smart_money"):
                    nansen_summary += " [🏷️ <b>Smart Money</b>]"
            if nansen_rep:
                nansen_score = nansen_rep.get("reputation_score", 5.0)
                trust_label = "GoPlus Trust" if is_goplus else "Nansen Trust"
                nansen_summary += f"\n• {trust_label}: <b>{nansen_score:.1f}/10.0</b>"
                if nansen_rep.get("is_known_scammer"):
                    nansen_summary += f"\n• 🚨 <b>KNOWN SCAMMER (Confidence: {nansen_rep.get('scam_confidence', 0.0):.0%})</b>"
                if nansen_rep.get("failed_contracts", 0) > 0:
                    nansen_summary += f"\n• Failed Contracts: <b>{nansen_rep.get('failed_contracts')}</b>"
        
        if nansen_summary:
            reply += nansen_summary
            
        red_flags = result.get('red_flags',[])[:3]
        if red_flags: reply += "\n\n🚩 <b>Red Flags:</b>\n" + "\n".join(f"• {escape_html(f)}" for f in red_flags)
        await msg.edit_text(reply, parse_mode="HTML")
    except Exception as e: logger.error(f"Deployer error: {e}"); await msg.edit_text(f"❌ Error: {escape_html(str(e))}", parse_mode="HTML")


async def smartmoney_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Backward-compatible wrapper – delegates to the new Degen Flow logic."""
    await degenflow_command(update, context)

async def degenflow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """DEGEN FLOW — honest risk feed. Sub‑commands: new, scan, or default trending."""
    if not await _require_private_chat(update): return
    if not await require_subscription(update, context): return

    args = context.args or []
    query = update.callback_query

    # ── Sub‑command routing ────────────────────────────────────────────
    if args and args[0].lower() == "new":
        await _degenflow_new_launches(update, context, args[1:])
        return

    if args and args[0].lower() == "scan":
        if len(args) < 2:
            if query: await query.message.edit_text("Usage: /degenflow scan &lt;address>")
            else: await update.message.reply_text("Usage: /degenflow scan &lt;address>")
            return
        context.args = args[1:]
        await scan_command(update, context, fast_mode=True)
        return

    # ── Default: trending feed (unchanged) ──────────────────────────────
    if query:
        msg = await query.message.edit_text("⚠️ Scanning Degen Flow…", parse_mode="HTML")
    else:
        msg = await update.message.reply_text("⚠️ Scanning Degen Flow…")

    try:
        tokens = await get_smart_money_tokens(config, debug=DEBUG)
        if not tokens:
            await msg.edit_text("No trending tokens found right now.")
            return

        for t in tokens:
            address = t.get("address", "")
            is_pump = address.endswith("pump") if address else False
            if t.get("security_score") is None:
                t["security_score"] = 8.0 if is_pump else 7.0
            if is_pump and t["security_score"] < 4.0:
                t["security_score"] = 4.0
            if t.get("liquidity_usd", 0) == 0 and t["security_score"] < 10.0:
                t["security_score"] = 10.0
            t["is_pumpfun"] = is_pump

        lines = ["⚠️ <b>DEGEN FLOW — HIGH RISK</b> ⚠️",
                 "CONTEXT: New tokens are high‑risk by default. Scores reflect on‑chain data (liquidity, holders, age).\n"]

        keyboard = []
        for t in tokens[:10]:
            address = t.get("address", "")
            symbol = t.get("symbol", "???")
            name = t.get("name", "Unknown")
            score = t.get("security_score", 8.0)
            label = _risk_label_from_score(score)
            is_pump = t.get("is_pumpfun", False)
            liq = t.get("liquidity_usd") or 0
            wallet_count = t.get("unique_wallets_24h") or 0
            holder_flag = "MAJORITY SUPPLY RISK – Assume Top10 >50%" if (is_pump or wallet_count == 0) else f"{wallet_count} wallets 24h"
            chain_tag = "SOLANA" if not is_pump else "PUMP.FUN"
            risk_prefix = f"🎰 {chain_tag} RISK: {score}/10 \"{label}\""

            lines.append(
                f"<b>{name}</b> ({symbol})\n"
                f"  {risk_prefix}\n"
                f"  💰 <b>BAG ALERT:</b> {holder_flag} | LIQ: ${liq:,.0f}\n"
                f"  🚨 <b>MAIN RISK:</b> {_main_risk_for_token(t)}\n"
                f"  📈 20tx: {t.get('price_change_24h',0):+.1f}%\n"
                f"  <code>{address[:10]}…{address[-4:]}</code>"
            )
            keyboard.append([InlineKeyboardButton(f"🔍 Audit {symbol}", callback_data=f"scan_sol_{address}")])

        keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data="degenflow_refresh")])
        context.user_data["degenflow_tokens"] = tokens
        await msg.edit_text("\n\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    except Exception as e:
        logger.error(f"DegenFlow error: {e}")
        await msg.edit_text(f"❌ Error: {escape_html(str(e))}")

# ── DEGEN FLOW “new” branch: full enrichment with LP + holders + authorities ──
async def _degenflow_new_launches(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_args: list):
    """Replaces /newtokens. Instant hard‑rule risk for newest Solana profiles."""
    filters = _parse_degenflow_filters(raw_args)
    query = update.callback_query
    if query:
        msg = await query.message.edit_text("🔍 Scanning new launches + checking LP locks…", parse_mode="HTML")
    else:
        msg = await update.message.reply_text("🔍 Scanning new launches + checking LP locks…")

    try:
        async with httpx.AsyncClient(timeout=8.0, headers={"accept": "application/json"}) as client:
            # 1. Fetch latest Solana profiles
            latest = await _fetch_dexscreener_latest_solana(client, limit=20)
            if not latest:
                await msg.edit_text("No new Solana tokens found in the last few minutes. Try again shortly.")
                return

            # 2. Enrich all tokens concurrently (full data)
            tasks = [_enrich_token_data_full(t, client) for t in latest]
            enriched_data = await asyncio.gather(*tasks, return_exceptions=True)

            scored = []
            for token, enrich in zip(latest, enriched_data):
                if isinstance(enrich, Exception):
                    # Fallback to basic enrichment if full fails
                    liq, age_min = await _enrich_token_data_fast(token, client)
                    token["liquidity_usd"] = liq
                    token["age_minutes"] = age_min
                    token["lp_locked"] = False
                    token["lp_lock_days"] = 0
                    token["lp_status"] = "Unknown"
                    token["top10_pct"] = 100.0
                    token["holder_count"] = 0
                    token["mint_revoked"] = None
                    token["freeze_revoked"] = None
                else:
                    token.update(enrich)

                risk_data = _calculate_degenflow_risk_full(token)
                token.update(risk_data)
                scored.append(token)

            # 3. Apply filters
            filtered = _apply_degenflow_filters(scored, filters)
            if not filtered:
                filter_str = " ".join(raw_args) if raw_args else "none"
                await msg.edit_text(
                    f"No tokens match filters: <code>{escape_html(filter_str)}</code>\n\n"
                    f"Note: New launches start at 8/10 risk. Try <code>/degenflow new max_risk:9</code>",
                    parse_mode="HTML"
                )
                return

            filtered.sort(key=lambda x: (x["risk_score"], -x["liquidity_usd"]))
            filtered = filtered[:10]

            # 4. Build output
            lines = [
                "⚠️ <b>DEGEN FLOW — NEW LAUNCHES</b> ⚠️",
                f"Filters: {_format_filters_display(filters)} | Sorted: Lowest Risk",
                "CONTEXT: 0–5 min old. 99.6% fail. Scores relative to other new launches.",
                ""
            ]

            for token in filtered:
                name = escape_html((token.get("name") or token["address"][:20])[:20])
                symbol = escape_html((token.get("symbol") or "???")[:8])
                addr = f"{token['address'][:8]}…{token['address'][-4:]}"
                liq_str = f"${token['liquidity_usd']:,.0f}" if token["liquidity_usd"] > 0 else "$0"
                age_str = f"{int(token['age_minutes'])}min" if token["age_minutes"] > 0 else "<1min"

                lines.extend([
                    f"{name} ({symbol}) {addr}",
                    f"🎰 <b>RISK: {token['risk_score']:.1f}/10.0 \"{token['risk_label']}\"</b>",
                    f"🔍 <b>CODE:</b> {token['code_summary']}",
                    f"💰 <b>BAG ALERT:</b> {token['bag_alert']}",
                    f"⏱️ <b>AGE:</b> {age_str} | <b>LIQ:</b> {liq_str} | <b>LP:</b> {token['lp_status']}",
                    f"🚨 <b>MAIN RISK:</b> {token['main_risk'][:120]}",
                    ""
                ])
            lines.append("Use <code>/degenflow scan CA</code> for full RugCheck/SolSniffer")
            context.user_data["degenflow_tokens"] = latest
            await msg.edit_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)

    except Exception as e:
        logger.error(f"DegenFlow new error: {e}")
        await msg.edit_text("Error fetching new launches. DexScreener may be rate‑limited. Try again.")

# ── Full enrichment: LP lock, holders, authorities ──────────────────────
async def _enrich_token_data_full(token: dict, client: httpx.AsyncClient) -> dict:
    """Fetch liquidity, age, LP locks, holders, authorities from DexScreener + RugCheck."""
    address = token.get("address") or token.get("tokenAddress", "")
    result = {
        "liquidity_usd": 0.0,
        "age_minutes": 0.0,
        "lp_locked": False,
        "lp_lock_days": 0,
        "lp_status": "Unknown",
        "top10_pct": 100.0,
        "holder_count": 0,
        "mint_revoked": None,
        "freeze_revoked": None,
    }
    if not address: return result

    dex_task = _fetch_dexscreener_pair_data(address, client)
    rugcheck_task = _fetch_rugcheck_data_fast(address, client)

    dex_data, rugcheck_data = await asyncio.gather(dex_task, rugcheck_task, return_exceptions=True)

    if not isinstance(dex_data, Exception) and dex_data:
        result["liquidity_usd"] = dex_data.get("liquidity_usd", 0.0)
        result["age_minutes"] = dex_data.get("age_minutes", 0.0)

    if not isinstance(rugcheck_data, Exception) and rugcheck_data:
        # LP Lock
        locks = rugcheck_data.get("locks", [])
        if locks:
            max_lock = max(locks, key=lambda x: x.get("unlockDate", 0))
            unlock_ts = (max_lock.get("unlockDate") or 0) / 1000
            if unlock_ts > datetime.now(timezone.utc).timestamp():
                result["lp_locked"] = True
                result["lp_lock_days"] = int((unlock_ts - datetime.now(timezone.utc).timestamp()) / 86400)
                result["lp_status"] = f"Locked {result['lp_lock_days']}d"
            else:
                result["lp_status"] = "Expired"
        else:
            result["lp_status"] = "Unlocked"

        # Holders
        result["top10_pct"] = float(rugcheck_data.get("topHoldersPct", 100.0))
        result["holder_count"] = int(rugcheck_data.get("totalHolders", 0))

        # Authorities
        result["mint_revoked"] = rugcheck_data.get("mintAuthorityRevoked")
        result["freeze_revoked"] = rugcheck_data.get("freezeAuthorityRevoked")

    if result["lp_status"] == "Unknown":
        result["lp_status"] = "Unlocked"
    return result

async def _fetch_dexscreener_pair_data(address: str, client: httpx.AsyncClient) -> dict:
    """Get liquidity + age from DexScreener pairs endpoint."""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
        resp = await client.get(url, timeout=3.0)
        if resp.status_code != 200: return {}
        data = resp.json(); pairs = data.get("pairs", [])
        if not pairs: return {}
        pair = pairs[0]; liq = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        created_at = (pair.get("pairCreatedAt") or 0) / 1000
        age_min = max(0.0, (datetime.now(timezone.utc).timestamp() - created_at) / 60) if created_at else 0.0
        return {"liquidity_usd": liq, "age_minutes": age_min}
    except Exception: return {}

async def _fetch_rugcheck_data_fast(address: str, client: httpx.AsyncClient) -> dict:
    """Fast RugCheck API call for locks, holders, authorities."""
    try:
        url = f"https://api.rugcheck.xyz/v1/tokens/{address}/report"
        resp = await client.get(url, timeout=3.0)
        if resp.status_code != 200: return {}
        return resp.json()
    except Exception: return {}

# ── Fallback basic enrichment (if full fails) ──────────────────────────
async def _enrich_token_data_fast(token: dict, client: httpx.AsyncClient) -> tuple[float, float]:
    """Fast enrichment: liquidity + age from DexScreener pairs. Returns (liq_usd, age_min)."""
    address = token.get("address") or token.get("tokenAddress", "")
    if not address: return 0.0, 0.0
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
        resp = await client.get(url, timeout=3.0)
        if resp.status_code != 200: return 0.0, 0.0
        data = resp.json(); pairs = data.get("pairs", [])
        if not pairs: return 0.0, 0.0
        pair = pairs[0]; liq = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        created_at = (pair.get("pairCreatedAt") or 0) / 1000
        age_min = max(0.0, (datetime.now(timezone.utc).timestamp() - created_at) / 60) if created_at else 0.0
        return liq, age_min
    except Exception: return 0.0, 0.0

async def _fetch_dexscreener_latest_solana(client: httpx.AsyncClient, limit: int = 20) -> list:
    """Fetch latest Solana token profiles from DexScreener (no auth)."""
    try:
        resp = await client.get("https://api.dexscreener.com/token-profiles/latest/v1", timeout=5.0)
        if resp.status_code != 200: return []
        data = resp.json()
        return [t for t in data if t.get("chainId") == "solana"][:limit]
    except Exception: return []

# ── Full risk scoring with LP + holder data ─────────────────────────────
def _calculate_degenflow_risk_full(token: dict) -> dict:
    """Full Degen Flow scoring with LP lock, top10%, authorities – additive version."""
    address = token.get("address", ""); is_pump = address.endswith("pump")
    liq = token.get("liquidity_usd", 0.0); age_min = token.get("age_minutes", 0.0)
    lp_locked = token.get("lp_locked", False); lp_status = token.get("lp_status", "Unlocked")
    top10_pct = token.get("top10_pct", 100.0); holder_count = token.get("holder_count", 0)
    mint_revoked = token.get("mint_revoked"); freeze_revoked = token.get("freeze_revoked")
    age_hours = age_min / 60.0

    # ── Additive scoring – start from 0 ───────────────────────────────
    score = 0.0
    flags = []

    if is_pump:
        score += 2.0

    # ---- Liquidity ----
    if liq == 0:
        score += 10.0
        flags.append('Zero liquidity – cannot sell')
    elif liq < 10_000:
        score += 3.0
        flags.append(f'Very thin liquidity (${liq:,.0f})')
    elif liq < 100_000:
        score += 1.0
        flags.append(f'Low liquidity (${liq:,.0f})')
    elif liq > 1_000_000:
        score -= 1.0
        flags.append(f'Deep liquidity (${liq:,.0f})')

    # ---- Age ----
    if age_hours < 1:
        score += 2.0
        flags.append('Brand new (<1 hour)')
    elif age_hours <= 24:
        score += 1.5
        flags.append(f'Very new ({int(age_hours)}h)')
    elif age_hours < 720:   # 30 days
        score += 0.5
        flags.append(f'Less than 30 days old')
    elif age_hours > 4320:  # 180 days
        score -= 1.0
        flags.append('Long track record (180+ days)')

    # ---- Holder concentration ----
    if top10_pct > 80:
        score += 4.0
        flags.append(f'CRITICAL concentration: Top10 hold {top10_pct:.0f}%')
    elif top10_pct > 70:
        score += 3.0
        flags.append(f'Highly concentrated: Top10 hold {top10_pct:.0f}%')
    elif top10_pct > 50:
        score += 1.0
        flags.append(f'Concentrated: Top10 hold {top10_pct:.0f}%')
    elif top10_pct > 30:
        score += 0.5
        flags.append(f'Elevated concentration: Top10 hold {top10_pct:.0f}%')
    elif top10_pct < 30:
        score -= 1.0
        flags.append(f'Widely distributed: Top10 hold {top10_pct:.0f}%')

    # ---- LP lock ----
    if not lp_locked and mint_revoked is False and freeze_revoked is False:
        # Managed token
        pass
    elif not lp_locked:
        score += 3.0
        flags.append('LP unlocked – liquidity can be removed')
    else:
        try:
            days = int(token.get("lp_lock_days", 0))
            if days > 180:
                score -= 1.0
                flags.append(f'LP locked for {days}d')
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
        label = 'LOW RISK – VERIFIED BY ON‑CHAIN DATA'

    # ---- Build summaries ----
    code_parts = ['Pump.fun' if is_pump else 'SPL Token']
    if mint_revoked is True: code_parts.append('Mint: Revoked')
    elif mint_revoked is False: code_parts.append('Mint: LIVE')
    if freeze_revoked is True: code_parts.append('Freeze: Revoked')
    elif freeze_revoked is False: code_parts.append('Freeze: LIVE')
    code_summary = ' | '.join(code_parts)

    bag_alert = f'Top10: {top10_pct:.0f}% | Holders: {holder_count}'
    if liq:
        bag_alert += f' | LIQ: ${liq:,.0f}'

    risk_flags = [f for f in flags if not f.startswith(('Deep liquidity', 'Long track record', 'Widely distributed', 'LP locked for', 'Mint authority revoked', 'Freeze authority revoked', 'Owner holds only'))]
    # Prioritise the most dangerous flag: critically low holders > LP unlocked > others
    _priority_prefixes = ('Critically low', 'Zero liquidity', 'Very thin', 'Brand new', 'Very new', 'Mint authority live', 'Freeze authority live', 'CRITICAL concentration', 'Highly concentrated', 'LP unlocked', 'Floor:')
    priority_flags = [f for f in risk_flags if any(f.startswith(p) for p in _priority_prefixes)]
    main_risk = priority_flags[0] if priority_flags else (risk_flags[0] if risk_flags else (flags[0] if flags else 'New token — treat as high risk until proven otherwise'))

    return {
        "risk_score": round(score, 1), "risk_label": label, "code_summary": code_summary,
        "bag_alert": bag_alert, "main_risk": main_risk, "lp_status": lp_status
    }

# ── Existing helpers (unchanged) ──────────────────────────────────────
def _risk_label_from_score(score: float) -> str:
    if score >= 10: return "INSTANT RUG – UNSWAPPABLE"
    if score >= 9:  return "EXTREME RISK – STANDARD PUMP.FUN RUG ODDS"
    if score >= 8:  return "HIGH RISK – DEGEN GAMBLE"
    if score >= 7:  return "GRADUATED BUT DANGEROUS"
    if score >= 6:  return "ELEVATED RISK"
    return "INSUFFICIENT DATA"

def _main_risk_for_token(t: dict) -> str:
    liq = t.get("liquidity_usd", 0) or 0
    is_pumpfun = t.get("is_pumpfun", False)

    if liq == 0:
        return "No liquidity – cannot sell"

    # Still on Pump.fun bonding curve → warn about rug odds
    if is_pumpfun:
        return "New token on Pump.fun bonding curve — objective risk depends on liquidity and holders"

    # Graduated SPL token → warn about holder concentration
    return f"Graduated token – Top10 concentration assumed with ${liq:,.0f} liquidity"

# ── Filter functions ───────────────────────────────────────────────────
def _parse_degenflow_filters(args: list) -> dict:
    filters = {"min_liq": 0.0, "min_age": 0.0, "max_risk": 10.0}
    for arg in args:
        if ":" in arg:
            key, val = arg.split(":", 1)
            try:
                if key == "min_liq": filters["min_liq"] = float(val)
                elif key == "min_age": filters["min_age"] = float(val)
                elif key == "max_risk": filters["max_risk"] = float(val)
            except ValueError: pass
    return filters

def _apply_degenflow_filters(tokens: list, filters: dict) -> list:
    return [
        t for t in tokens
        if t["liquidity_usd"] >= filters["min_liq"]
        and t["age_minutes"] >= filters["min_age"]
        and t["risk_score"] <= filters["max_risk"]
    ]

def _format_filters_display(filters: dict) -> str:
    parts = []
    if filters["min_liq"] > 0: parts.append(f"liq ≥${filters['min_liq']:,.0f}")
    if filters["min_age"] > 0: parts.append(f"age ≥{filters['min_age']}min")
    if filters["max_risk"] < 10: parts.append(f"risk ≤{filters['max_risk']}")
    return " | ".join(parts) if parts else "none"

# ─────────────────────────── Callback handler ───────────────────────────
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data
    if data.startswith("cryptomus_pay_"):
        tier = data.split("_")[-1]  # monthly or yearly
        if tier == "monthly":
            days = 30
            amount_usd = 79.0
        else:
            days = 365
            amount_usd = 699.0
        order_id = str(uuid.uuid4())
        
        user_id = update.effective_user.id
        from core.db import create_cryptomus_order
        from core.cryptomus import create_payment_invoice
        
        await create_cryptomus_order(order_id, user_id, amount_usd, days)
        url = await create_payment_invoice(order_id, amount_usd)
        
        if url:
            keyboard = [[InlineKeyboardButton("Pay Now via Cryptomus", url=url)]]
            await query.message.edit_text(
                f"<b>Secure Checkout Generated</b>\n\n"
                f"Tier: {days} Days\nAmount: ${amount_usd:.2f}\n\n"
                f"<i>Click the button below to complete your payment. Your subscription will activate automatically upon confirmation.</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await query.message.edit_text("❌ Failed to generate checkout link. Please try again later.")
    elif data == "sample_scan":
        await query.edit_message_text("🔍 Running a sample audit on USDC (Solana)...")
        await scan_command(update, context, fast_mode=True)
    elif data == "cmd_scan_prompt":
        await query.answer()
        await query.message.edit_text(
            "<b>🔍 Scan a Contract</b>\n\n"
            "Type <code>/scan 0x... chain</code> to audit any smart contract.\n\n"
            "<b>Examples:</b>\n"
            "<code>/scan 0x7a25... eth</code> – Ethereum\n"
            "<code>/scan Hcbs... solana</code> – Solana\n"
            "<code>/scan 0x10ED... bsc</code> – BSC\n\n"
            "<i>Chain is auto‑detected for Solana (base58) and EVM (0x).</i>",
            parse_mode="HTML",
        )

    elif data == "cmd_deployer_prompt":
        await query.answer()
        await query.message.edit_text(
            "<b>🕵️ Deployer Check</b>\n\n"
            "Type <code>/deployer 0x...</code> to trace a wallet's full contract‑creation history across multiple chains.\n\n"
            "<b>Example:</b>\n"
            "<code>/deployer 0xedc2...6dc4</code>\n\n"
            "<i>See every token they ever deployed, reputation score, and red flags.</i>",
            parse_mode="HTML",
        )
    elif data == "cmd_degenflow": await degenflow_command(update, context)
    elif data == "cmd_subscribe":
        await query.answer()
        await query.message.edit_text("<b>💎 Subscribe to Aegis Premium</b>\n\nTo subscribe, type /subscribe in the chat.\n\nYou'll see the live token price and the exact amount of $AEGIS to send.", parse_mode="HTML")
    elif data == "cmd_trust":
        await trust_command(update, context)
    elif data == "cmd_compare":
        await compare_command(update, context)
    elif data == "cmd_help":
        await query.answer()
        await query.message.edit_text(
            "<b>🛡️ Aegis Commands</b>\n\n"
            "<b>/scan &lt;address&gt; [chain]</b> — Fast security audit\n"
            "<b>/deepscan &lt;address&gt; [chain]</b> — Extended analysis\n"
            "<b>/deployer &lt;address&gt;</b> — Deployer forensics\n"
            "<b>/degenflow</b> — Solana risk feed\n"
            "<b>/trust</b> — The Aegis Trust Manifesto\n"
            "<b>/compare</b> — Aegis vs. Traditional Bots\n"
            "<b>/subscribe</b> — Purchase premium\n"
            "<b>/status</b> — Check trial/subscription\n"
            "<b>/start</b> — Dashboard\n\n"
            "<i>All premium commands require an active subscription or trial.</i>",
            parse_mode="HTML",
        )
    elif data == "cmd_status":
        await query.answer()
        user_id = update.effective_user.id
        db_user = await get_or_create_user(user_id)
        allowed, reason = can_use_service(db_user)
        if reason == "trial":
            ends = datetime.fromisoformat(db_user["trial_ends_at"]).strftime("%Y-%m-%d %H:%M UTC")
            text = f"<b>🆓 Free Trial</b>\nEnds: {escape_html(ends)}"
        elif reason == "subscribed":
            ends = datetime.fromisoformat(db_user["subscription_expires_at"]).strftime("%Y-%m-%d %H:%M UTC")
            wallet = db_user.get("wallet_address", "Not set")
            text = f"<b>✅ Premium Active</b>\nExpires: {escape_html(ends)}\nWallet: <code>{escape_html(wallet)}</code>"
        else:
            text = "<b>⛔ No active subscription</b>\nUse /subscribe to purchase."
        await query.message.edit_text(text, parse_mode="HTML")
    elif data == "degenflow_refresh": await degenflow_command(update, context)
    elif data == "radar_refresh": await degenflow_command(update, context)
    elif data == "smartmoney_refresh": await degenflow_command(update, context)
    elif data == "newtokens_refresh":
        context.args = ["new"]
        await degenflow_command(update, context)
    elif data.startswith("scan_sol_"):
        address = data.replace("scan_sol_", "")

        # Look up token name from the stored DEGEN FLOW feed
        tokens = context.user_data.get("degenflow_tokens", [])
        token_meta = next((t for t in tokens if t.get("address") == address), None)
        if token_meta:
            display_name = escape_html(token_meta.get("name") or address[:12] + "…")
            display_symbol = escape_html(token_meta.get("symbol") or "???")
        else:
            display_name = escape_html(address[:12] + "…")
            display_symbol = "???"

        await query.answer(f"Auditing {display_name}...")
        msg = await query.message.reply_text(
            f"🔍 Auditing {display_name} on Solana...",
            parse_mode="HTML"
        )
        try:
            contract, result = await run_scan(address, "solana", config, fast_mode=True)
            raw = result.get("_raw", {})
            risk = await calculate_degen_risk_solana(raw, address, fast_mode=True)
            deployer = contract.get("deployer_address")

            lines = [
                f"<b>{display_name} ({display_symbol})</b>",
                f"<code>{escape_html(address)}</code>",
            ]
            if deployer:
                lines.append(f"🕵️ <b>DEPLOYER:</b> <code>{escape_html(deployer)}</code>")
                
            lines.extend([
                "",
                f"🎰 <b>AEGIS SCORE: {risk['score']:.1f}/10.0 \"{risk['label']}\"</b>",
                f"💰 <b>BAG ALERT:</b> {risk['bag_alert']}",
                f"🔍 <b>CODE:</b> {risk['code_summary']}",
                f"⏱️ <b>AGE:</b> {risk['age_str']} | <b>LIQ:</b> {risk['liq_str']} | <b>LP:</b> {risk['lp_status']}",
                f"🚨 <b>MAIN RISK:</b> {risk['main_risk']}",
            ])
            if risk.get("flags"):
                lines.append("")
                lines.append("<b>DETAILED FLAGS:</b>")
                for f in risk["flags"][:10]:
                    lines.append(f"• {escape_html(f)}")

            is_pumpfun = address.endswith("pump") if address else False
            if is_pumpfun:
                lines.append("")
                lines.append("⚠️ <b>CONTEXT:</b> 99% of Pump.fun tokens die. Score relative to other rugs.")

            reply = "\n".join(lines)
            sanitize_output(reply)
            
            keyboard = []
            if deployer:
                keyboard.append([InlineKeyboardButton("🕵️ Analyze Deployer Wallet", callback_data=f"deployer_check_{deployer}")])
                
            await msg.edit_text(reply, reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None, parse_mode="HTML")
        except Exception as e:
            await msg.edit_text(f"❌ Audit failed: {escape_html(str(e))}")
    elif data.startswith("deployer_check_"):
        address = data.replace("deployer_check_", "")
        await query.answer(f"Auditing deployer {address[:8]}...")
        context.args = [address]
        await deployer_command(update, context)
    elif data.startswith("pdf_"):
        address = data[4:]
        result = context.user_data.get('last_scan_result', {})
        contract = context.user_data.get('last_scan_contract', {})
        chain = context.user_data.get('last_scan_chain', 'eth')
        await query.answer("Generating PDF…")
        try:
            pdf_bytes = generate_audit_pdf(address, chain, result, contract)
            pdf_file = io.BytesIO(pdf_bytes); pdf_file.name = f"Aegis_Audit_{address[:10]}_{chain}.pdf"
            await context.bot.send_document(chat_id=update.effective_chat.id, document=pdf_file, caption=f"🛡️ Aegis Security Audit Report\n{address}\nChain: {chain.upper()}", filename=pdf_file.name)
        except Exception as e: await query.message.reply_text(f"❌ Failed to generate PDF: {escape_html(str(e))}")

    else:
        await query.edit_message_text("I can explain that further. What specifically would you like to know?", parse_mode="HTML")

# ─────────────────────────── Admin & Legal ───────────────────────────
async def terms_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📜 <b>Terms of Service & Disclaimer</b>\n\n"
        "Aegis is an automated security analysis tool. By using Aegis, you agree to the following:\n\n"
        "1. <b>Not Financial Advice:</b> Aegis does not provide financial advice. Risk scores are based purely on automated on-chain metrics.\n"
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

async def clear_cache_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear both in-memory and Redis caches."""
    try:
        # Clear local memory cache
        from utils.cache import _memory_cache
        _memory_cache.clear()

        # Clear Redis cache
        try:
            import redis.asyncio as aioredis
            from utils.cache import REDIS_URL
            if REDIS_URL:
                r = aioredis.from_url(REDIS_URL)
                await r.flushall()
                if hasattr(r, "aclose"):
                    await r.aclose()
                else:
                    await r.close()
        except Exception:
            pass

        await update.message.reply_text("🧹 Cache cleared successfully!")
    except Exception as e:
        await update.message.reply_text(f"❌ Error clearing cache: {e}")

# ─────────────────────────── Error handler ───────────────────────────
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)

# ─────────────────────────── Main ──────────────────────────────────────
async def run_fastapi_server(port: int):
    """Run FastAPI as an asyncio task alongside the bot."""
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

async def main_async():
    """Initialize the bot and start FastAPI in the same event loop."""
    await init_db()

    try:
        from fetchers.etherscan import init_etherscan_pool
        etherscan_keys = config["explorers"].get("etherscan", [])
        if etherscan_keys:
            init_etherscan_pool(etherscan_keys, calls_per_second=5.0)
    except Exception as e:
        logger.warning(f"Etherscan pool init: {e}")

    token = config["telegram"]["bot_token"]
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN missing")
        sys.exit(1)

    application = Application.builder().token(token).build()

    for cmd, handler in [
        ("start", start), ("help", help_command), ("subscribe", subscribe_command),
        ("status", status_command),
        ("degenflow", degenflow_command), ("flow", degenflow_command),
        ("new", degenflow_command), ("newtoken", degenflow_command),
        ("newtokens", degenflow_command), ("radar", degenflow_command),
        ("smartmoney", degenflow_command),
        ("scan", lambda u, c: scan_command(u, c, True)), ("deepscan", deepscan_command),
        ("deployer", deployer_command),
        ("trust", trust_command),
        ("compare", compare_command),
        ("terms", terms_command),
        ("grant", grant_command),
        ("revoke", revoke_command),
        ("clear_cache", clear_cache_command),
        ("flush", clear_cache_command),
    ]:
        application.add_handler(CommandHandler(cmd, handler))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_error_handler(error_handler)

    # ── Dynamic Webhook or Polling mode ─────────────────────────────────────
    logger.info("Starting Aegis Telegram Bot…")
    await application.initialize()
    await application.start()

    import sys
    force_polling = os.getenv("FORCE_POLLING", "false").lower() == "true" or "--polling" in sys.argv
    webhook_url = config.get("webhook", {}).get("base_url", "")
    if webhook_url and "localhost" not in webhook_url and "127.0.0.1" not in webhook_url and not force_polling:
        webhook_path = f"{webhook_url.rstrip('/')}/webhook"
        logger.warning(f"Setting Telegram Webhook to: {webhook_path}")
        await application.bot.set_webhook(url=webhook_path, allowed_updates=Update.ALL_TYPES)
    else:
        logger.warning("Starting bot in standard polling mode...")
        await application.bot.delete_webhook()
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)

    # ── Run FastAPI in the same event loop ─────────────────────────────────
    port = int(os.getenv("PORT", "8000"))
    logger.info(f"Starting FastAPI on port {port}…")
    await run_fastapi_server(port)


if __name__ == "__main__":
    asyncio.run(main_async())
