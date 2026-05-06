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
from core.wallet_session import run_wallet_tracker
from core.subscription import (
    get_or_create_user, can_use_service,
    process_verification, usage_logger, payment_verifier
)
from utils.validators import is_solana_address, is_evm_address
from services.smartmoney import get_smart_money_tokens
from services.newtokens import get_new_tokens
from services.pdf_report import generate_audit_pdf
from api import app

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
        f"🏥 <b>Portfolio Health:</b> AI Wallet Audit\n"
        f"🧠 <b>Entity Intelligence:</b> Deployer Forensics\n\n"
        f"🛡️ <b>AEGIS VERIFIED:</b> {status_color} {escape_html(status)}\n\n"
        f"Welcome back, {name}. System status: NOMINAL."
    )
    keyboard = [
        [InlineKeyboardButton("🔍 Scan Contract", callback_data="cmd_scan_prompt"),
         InlineKeyboardButton("⚠️ DEGEN FLOW", callback_data="cmd_degenflow")],
        [InlineKeyboardButton("💼 Wallet Audit", callback_data="cmd_wallet_prompt"),
         InlineKeyboardButton("🕵️ Deployer Check", callback_data="cmd_deployer_prompt")],
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
    try:
        price = await payment_verifier.get_token_price()
        required = await payment_verifier.required_tokens()
    except Exception:
        await update.message.reply_text("❌ Could not fetch token price. Try again later.")
        return
    receiver = str(payment_verifier.payment_receiver)
    msg = (
        f"<b>💎 Subscribe to Aegis Premium</b>\n\n"
        f"Monthly price: <b>${config['subscription']['price_usd']:.2f}</b>\n"
        f"Current $AEGIS price: <code>{price:.6f}</code>\n"
        f"Tokens required: <b>{required:.2f} $AEGIS</b>\n\n"
        f"1️⃣ Send exactly the required amount to:\n<code>{escape_html(receiver)}</code>\n\n"
        f"2️⃣ After sending, use: <code>/verify &lt;transaction_signature&gt;</code>\n\n"
        f"<i>Note:</i> 60% burned 🔥, 40% to treasury 💰"
    )

async def verify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_private_chat(update): return
    args = context.args
    if not args: await update.message.reply_text("Usage: /verify <tx_signature>"); return
    signature = args[0].strip(); user_id = update.effective_user.id
    msg = await update.message.reply_text("⌛ Verifying transaction on Solana...")
    result = await process_verification(user_id, signature)
    if not result["success"]:
        await msg.edit_text(f"❌ Verification failed: {escape_html(result['error'])}", parse_mode="HTML"); return
    expires = result["expires_at"].strftime("%Y-%m-%d")
    split_info = f"\nSplit tx: <code>{escape_html(result['split_tx'])}</code>" if result.get("split_tx") else ""
    await msg.edit_text(
        f"❅ \u003cb\u003ePayment verified!\u003c/b\u003e\nSubscription active until \u003cb\u003e{escape_html(expires)}\u003c/b\u003e.\n"
        f"60% burned 🔥, 40% to treasury 💰{split_info}", parse_mode="HTML"
    )
    if config["telegram"]["admin_user_id"]:
        try: await context.bot.send_message(config["telegram"]["admin_user_id"], f"💰 New subscription: User {user_id} paid for 30 days.")
        except Exception: pass

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
    holder_count = len(holders)
    dev_pct = None

    # Fallback to RugCheck holder count if Helius fails (common for massive tokens like USDC)
    total_holders = rugcheck.get('total_holder_count') or rugcheck.get('totalHolders') or 0
    if total_holders and not holders:
        holder_count = int(total_holders)

    if holders:
        top10_pct = sum(h.get('percentage', 0) for h in holders[:10])
        dev_pct = holders[0].get('percentage', None) if holders else None

    # ── LP lock from RugCheck ─────────────────────────────────────────
    lp_locked = False
    lp_lock_days = 0
    locks = rugcheck.get('locks', [])
    if locks:
        max_lock = max(locks, key=lambda x: x.get("unlockDate", 0))
        unlock_ts = (max_lock.get('unlockDate') or 0) / 1000
        if unlock_ts > datetime.now(timezone.utc).timestamp():
            lp_locked = True
            lp_lock_days = int((unlock_ts - datetime.now(timezone.utc).timestamp()) / 86400)

    # ── Additive scoring – start from 0 ───────────────────────────────
    score = 0.0
    flags = []

    # ── Base‑rate adjustment for Pump.fun origin ────────────────────────
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
        score -= 2.0
        flags.append(f'Deep liquidity (${liq:,.0f})')

    # ---- Age ----
    if age_hours != 999:
        if age_hours < 1:
            score += 2.0
            flags.append('Brand new (<1 hour)')
        elif age_hours < 24:
            score += 1.5
            flags.append(f'Very new ({int(age_hours)}h)')
        elif age_hours < 720:   # 30 days
            score += 0.5
            flags.append(f'Less than 30 days old')
        elif age_hours > 4320:  # 180 days
            score -= 2.0
            flags.append('Long track record (180+ days)')
        elif age_hours > 8760:  # 365 days
            score -= 3.0
            # Already flagged above; just adjust
            pass

    # ---- Holder concentration (only if data available) ----
    if top10_pct is not None:
        if top10_pct > 70:
            score += 3.0
            flags.append(f'Highly concentrated: Top10 hold {top10_pct:.0f}%')
        elif top10_pct > 50:
            score += 1.0
            flags.append(f'Concentrated: Top10 hold {top10_pct:.0f}%')
        elif top10_pct < 30:
            score -= 1.0
            flags.append(f'Widely distributed: Top10 hold {top10_pct:.0f}%')

    # ---- LP lock ----
    if not lp_locked and not mint_revoked and not freeze_revoked:
        # LP unlocked but authorities are also live – this is normal for managed tokens
        # Don't penalise; just note it
        pass
    elif not lp_locked:
        score += 3.0
        flags.append('LP unlocked – liquidity can be removed')
    elif lp_lock_days > 180:
        score -= 1.0
        flags.append(f'LP locked for {lp_lock_days}d')

    # ---- Authorities ----
    if not mint_revoked:
        score += 2.0
        flags.append('Mint authority live – supply can be increased')
    else:
        score -= 1.0
        flags.append('Mint authority revoked')

    if not freeze_revoked:
        score += 2.0
        flags.append('Freeze authority live – tokens can be frozen')
    else:
        score -= 1.0
        flags.append('Freeze authority revoked')

    # ---- Final clamping ----
    score = max(0.0, min(10.0, score))

    # ---- Label ----
    if liq == 0:
        label = 'INSTANT RUG – UNSWAPPABLE'
    elif score >= 9.0:
        label = 'EXTREME RISK – LIKELY RUG'
    elif score >= 7.0:
        label = 'HIGH RISK – DEGEN GAMBLE'
    elif score >= 5.0:
        label = 'ELEVATED RISK – SPECULATIVE'
    elif score >= 3.0:
        label = 'MODERATE RISK – NEW TOKEN'
    elif score <= 1.0:
        label = 'LOW RISK – VERIFIED BY ON‑CHAIN DATA'
    else:
        label = 'LOW RISK – ESTABLISHED ASSET'

    # ---- Build summaries ----
    code_parts = ['Pump.fun' if ca.endswith('pump') else 'SPL Token']
    code_parts.append('Mint: Revoked' if mint_revoked else 'Mint: LIVE')
    code_parts.append('Freeze: Revoked' if freeze_revoked else 'Freeze: LIVE')
    code_summary = ' | '.join(code_parts)

    if holders:
        bag_alert = f'Top10: {top10_pct:.0f}% | Holders: {holder_count}'
    elif holder_count:
        bag_alert = f'Total holders: {holder_count} (distribution unverified)'
    else:
        bag_alert = 'Holder data unavailable'
    if liq:
        bag_alert += f' | LIQ: ${liq:,.0f}'

    lp_status = f'Locked {lp_lock_days}d' if lp_locked else 'Unlocked'
    age_str = f'{int(age_hours)}h' if age_hours < 72 else f'{int(age_hours/24)}d' if age_hours < 999 else '?'

    main_risk = flags[0] if flags else 'Insufficient data – see flags below'

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
    owner_pct = float(goplus.get('gp_owner_percent')) if goplus.get('gp_owner_percent') is not None else None
    lp_locked = not goplus.get('gp_cannot_sell_all', False)
    if not goplus.get('goplus_available'):
        owner_pct = None
        lp_locked = None

    # ── Additive scoring ───────────────────────────────────────────────
    score = 0.0
    flags = []

    # Honeypot → immediate 10
    if honeypot:
        return make_risk_dict(10.0, 'CONFIRMED HONEYPOT', 'GoPlus: Honeypot detected',
                              'Cannot sell – Honeypot confirmed', '?', '$?', 'None',
                              'Honeypot contract. You cannot sell.', '',
                              ['GoPlus: is_honeypot = true'], dex_token_name, dex_token_symbol)

    # Liquidity
    if liq == 0:
        score += 10.0
        flags.append('Zero liquidity – cannot sell')
    elif liq < 10_000:
        score += 3.0
        flags.append(f'Very thin liquidity (${liq:,.0f})')
    elif liq > 1_000_000:
        score -= 2.0
        flags.append(f'Deep liquidity (${liq:,.0f})')

    # ---- Age ----
    if age_hours != 999 and isinstance(age_hours, (int, float)):
        if age_hours < 1:
            score += 2.0
            flags.append('Brand new (<1 hour)')
        elif age_hours < 24:
            score += 1.5
            flags.append(f'Very new ({int(age_hours)}h)')
        elif age_hours < 720:
            score += 0.5
            flags.append(f'Less than 30 days old')
        elif age_hours > 4320:
            score -= 2.0
            flags.append('Long track record (180+ days)')

    # Owner concentration (only if GoPlus returned data)
    if owner_pct is not None:
        if owner_pct > 40:
            score += 2.0
            flags.append(f'Owner holds {owner_pct:.0f}%')
        elif owner_pct > 20:
            score += 1.0
            flags.append(f'Owner holds {owner_pct:.0f}%')
        else:
            score -= 1.0
            flags.append(f'Owner holds only {owner_pct:.0f}%')

    # LP lock
    if lp_locked is not None:
        if not lp_locked:
            score += 2.0
            flags.append('LP unlocked – liquidity can be removed')

    # Mintable
    if mintable is True:
        score += 2.0
        flags.append('Mint function exists – supply can be inflated')

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

    # Final clamping
    score = max(0.0, min(10.0, score))

    # Label
    if liq == 0:
        label = 'INSTANT RUG – UNSWAPPABLE'
    elif score >= 9.0:
        label = 'EXTREME RISK – LIKELY RUG'
    elif score >= 7.0:
        label = 'HIGH RISK – DEGEN GAMBLE'
    elif score >= 5.0:
        label = 'ELEVATED RISK – SPECULATIVE'
    elif score >= 3.0:
        label = 'MODERATE RISK – NEW TOKEN'
    elif score <= 1.0:
        label = 'LOW RISK – VERIFIED BY ON‑CHAIN DATA'
    else:
        label = 'LOW RISK – ESTABLISHED ASSET'

    # Summaries
    code_summary = f"Mintable: {mintable} | Owner: {owner_pct}%" if owner_pct is not None else "Tokenomics data unavailable"
    bag_alert = f'Owner: {owner_pct:.0f}%' if owner_pct is not None else 'Owner data unavailable'
    if liq:
        bag_alert += f' | LIQ: ${liq:,.0f}'
    lp_status_str = 'Locked' if lp_locked else 'Unlocked' if lp_locked is not None else 'Unknown'
    age_str = f'{int(age_hours)}h' if age_hours < 72 else f'{int(age_hours/24)}d' if age_hours < 999 else '?'

    return make_risk_dict(round(score, 1), label, code_summary, bag_alert,
                          age_str, f'${liq:,.0f}' if liq else '$0', lp_status_str,
                          flags[0] if flags else 'Insufficient data',
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
        usage = "Usage: /scan <address> [chain]\nChain is auto‑detected for Solana (base58) and EVM (0x)."
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

    # ── Build DEGEN output ─────────────────────────────────────────────
    # Token name from contract data (fallback to address)
    evm_token_name = contract.get('token_name') or risk.get('dex_name') or address[:12] + '…'
    evm_token_symbol = contract.get('token_symbol') or risk.get('dex_symbol') or '???'
    token_name = escape_html(evm_token_name)
    token_symbol = escape_html(evm_token_symbol)

    age_str = risk.get('age_str', '?')
    # If age is unknown but the token has deep liquidity and a low score, label as Established
    if age_str == '?' and risk.get('score', 10.0) <= 2.0 and risk.get('liq_str', '$0') != '$0':
        age_str = 'Established (exact age unavailable)'

    lines = [
        f"<b>{token_name} ({token_symbol})</b>",
        f"<code>{escape_html(address)}</code>",
        "",
        f"🎰 <b>{chain.upper()} RISK: {risk['score']:.1f}/10 \"{risk['label']}\"</b>",
        f"💰 <b>BAG ALERT:</b> {risk['bag_alert']}",
        f"🔍 <b>CODE:</b> {risk['code_summary']}",
        f"⏱️ <b>AGE:</b> {age_str} | <b>LIQ:</b> {risk['liq_str']} | <b>LP:</b> {risk['lp_status']}",
        f"🚨 <b>MAIN RISK:</b> {risk['main_risk']}",
    ]

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
    if not args: await update.message.reply_text("Usage: /deployer <address>"); return
    address = args[0]; user_id = update.effective_user.id; await usage_logger(user_id, "deployer", address)
    msg = await update.message.reply_text(f"🔍 Analyzing deployer <code>{escape_html(address)}</code> across 5 chains...", parse_mode="HTML")
    try:
        profile, result = await run_deployer_analysis(address, config, chains=["eth","bsc","polygon","base","arb"], stream=False, debug=DEBUG)
        score = result.get('reputation_score', result.get('risk_score','N/A')); verdict = result.get('verdict','N/A'); rec = result.get('recommendation','N/A')
        summary = result.get('summary','No summary.'); total = profile.get('total_deployments',0)
        reply = (
            f"<b>🕵️ Deployer Forensics</b>\nWallet: <code>{escape_html(address)}</code>\nContracts deployed: {total}\n\n"
            f"Reputation Score: <b>{score}/100</b>\nVerdict: <b>{escape_html(verdict)}</b>\nRecommendation: <b>{escape_html(rec)}</b>\n\n"
            f"📝 <b>Summary:</b>\n{escape_html(summary)}"
        )
        red_flags = result.get('red_flags',[])[:3]
        if red_flags: reply += "\n\n🚩 <b>Red Flags:</b>\n" + "\n".join(f"• {escape_html(f)}" for f in red_flags)
        await msg.edit_text(reply, parse_mode="HTML")
    except Exception as e: logger.error(f"Deployer error: {e}"); await msg.edit_text(f"❌ Error: {escape_html(str(e))}", parse_mode="HTML")

async def wallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_private_chat(update): return
    if not await require_subscription(update, context): return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /wallet <address>")
        return

    address = args[0]
    user_id = update.effective_user.id
    await usage_logger(user_id, "wallet", address)

    msg = await update.message.reply_text(
        f"💼 Scanning wallet <code>{escape_html(address)}</code> across all chains...",
        parse_mode="HTML"
    )

    try:
        data = await run_wallet_tracker(address, None, config, debug=DEBUG)
        snapshot = data["wallet_snapshot"]
        analysis = data["portfolio_analysis"]
        audited = data["audited_holdings"]
        all_tokens = snapshot.get("all_tokens", [])
        total_value = snapshot.get("total_value_usd", 0)
        total_tokens = snapshot.get("total_tokens", 0)

        context.user_data["wallet_tokens"] = all_tokens
        context.user_data["wallet_page"] = 0

        score = analysis.get("portfolio_risk_score", "N/A") if analysis else "N/A"
        grade = analysis.get("risk_grade", "?") if analysis else "?"
        summary = analysis.get("summary", "No analysis available.") if analysis else "No analysis available."
        pct_high = analysis.get("pct_high_risk", 0) if analysis else 0
        pct_safe = analysis.get("pct_safe", 0) if analysis else 0
        concentration = analysis.get("concentration_risk", False) if analysis else False

        # ▸ Header
        reply = (
            f"<b>💼 Wallet Analysis</b>\n"
            f"Address: <code>{escape_html(address)}</code>\n"
            f"Total Value: ${total_value:,.2f}\n"
            f"Tokens Found: {total_tokens}\n"
            f"Audited (top 10 by value): {len(audited)}\n\n"
            f"<b>Portfolio Risk Score:</b> {score}/10 (Grade {grade})\n\n"
            f"📝 <b>Summary:</b>\n{escape_html(summary)}"
        )

        if data.get("_cached"):
            reply += "\n\n<i>(served from cache)</i>"

        # ▸ Risk breakdown
        reply += (
            f"\n\n<b>Risk Breakdown</b>\n"
            f"• High‑risk holdings: {pct_high:.0f}%\n"
            f"• Safe holdings: {pct_safe:.0f}%\n"
            f"• Concentration risk: {'⚠️ Yes' if concentration else '✅ No'}"
        )

        # ▸ Top audited holdings table
        if audited:
            reply += "\n\n<b>Top Holdings (audited)</b>\n"
            reply += "<pre>"
            reply += f"{'Token':<20} {'Chain':<8} {'Value':>12} {'%':>6} {'Score':>6} {'Verdict':>10}\n"
            reply += "─" * 70 + "\n"
            for h in audited[:10]:
                name = (h.get("token_name") or h.get("name") or h.get("token_address", "")[:8] + "…")[:18]
                raw_chain = h.get("chain")
                if isinstance(raw_chain, dict):
                    chain = (raw_chain.get("slug") or raw_chain.get("name") or "?").upper()[:6]
                elif isinstance(raw_chain, str) and raw_chain:
                    chain = raw_chain.upper()[:6]
                else:
                    chain = "?"
                value = float(h.get("usd_value", 0))
                pct = (value / total_value * 100) if total_value > 0 else 0
                score_val = h.get("audit", {}).get("risk_score", "?")
                raw_verdict = h.get("audit", {}).get("recommendation", "?")
                if len(raw_verdict) > 10:
                    verdict = raw_verdict[:9] + "…"
                else:
                    verdict = raw_verdict
                reply += f"{name:<20} {chain:<8} ${value:>10,.2f} {pct:>5.1f}% {score_val:>5}  {verdict:>10}\n"
            reply += "</pre>"

        # ▸ High‑risk flags
        critical = analysis.get("critical_holdings", []) if analysis else []
        if critical:
            reply += "\n\n⚠️ <b>High‑Risk Holdings:</b>\n"
            for addr in critical[:3]:
                for h in audited:
                    if h.get("token_address") == addr:
                        name = h.get("token_name") or h.get("name") or addr[:10]
                        score_val = h.get("audit", {}).get("risk_score", "?")
                        reply += f"• {escape_html(name)} (score: {score_val}/10)\n"
                        break

        keyboard = _build_token_keyboard(all_tokens, page=0)
        await msg.edit_text(
            reply,
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Wallet error: {e}")
        await msg.edit_text(f"❌ Error: {escape_html(str(e))}", parse_mode="HTML")

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
            if query: await query.message.edit_text("Usage: /degenflow scan <address>")
            else: await update.message.reply_text("Usage: /degenflow scan <address>")
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
                    f"🎰 <b>RISK: {token['risk_score']:.1f}/10 \"{token['risk_label']}\"</b>",
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
        score -= 2.0
        flags.append(f'Deep liquidity (${liq:,.0f})')

    # ---- Age ----
    if age_hours < 1:
        score += 2.0
        flags.append('Brand new (<1 hour)')
    elif age_hours < 24:
        score += 1.5
        flags.append(f'Very new ({int(age_hours)}h)')
    elif age_hours < 720:   # 30 days
        score += 0.5
        flags.append(f'Less than 30 days old')
    elif age_hours > 4320:  # 180 days
        score -= 2.0
        flags.append('Long track record (180+ days)')
    elif age_hours > 8760:  # 365 days
        score -= 3.0
        # Already flagged above; just adjust
        pass

    # ---- Holder concentration ----
    if top10_pct > 70:
        score += 3.0
        flags.append(f'Highly concentrated: Top10 hold {top10_pct:.0f}%')
    elif top10_pct > 50:
        score += 1.0
        flags.append(f'Concentrated: Top10 hold {top10_pct:.0f}%')
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

    # ---- Authorities ----
    if mint_revoked is False:
        score += 2.0
        flags.append('Mint authority live – supply can be increased')
    elif mint_revoked is True:
        score -= 1.0
        flags.append('Mint authority revoked')

    if freeze_revoked is False:
        score += 2.0
        flags.append('Freeze authority live – tokens can be frozen')
    elif freeze_revoked is True:
        score -= 1.0
        flags.append('Freeze authority revoked')

    # ---- Final clamping ----
    score = max(0.0, min(10.0, score))

    # ---- Label ----
    if liq == 0:
        label = 'INSTANT RUG – UNSWAPPABLE'
    elif score >= 9.0:
        label = 'EXTREME RISK – LIKELY RUG'
    elif score >= 7.0:
        label = 'HIGH RISK – DEGEN GAMBLE'
    elif score >= 5.0:
        label = 'ELEVATED RISK – SPECULATIVE'
    elif score >= 3.0:
        label = 'MODERATE RISK – NEW TOKEN'
    elif score <= 1.0:
        label = 'LOW RISK – VERIFIED BY ON‑CHAIN DATA'
    else:
        label = 'LOW RISK – ESTABLISHED ASSET'

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

    main_risk = flags[0] if flags else 'New token — treat as high risk until proven otherwise'

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
    if data == "sample_scan":
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
    elif data == "cmd_wallet_prompt":
        await query.answer()
        await query.message.edit_text(
            "<b>💼 Wallet Audit</b>\n\n"
            "Type <code>/wallet 0x...</code> to audit any wallet's portfolio across all chains.\n\n"
            "<b>Example:</b>\n"
            "<code>/wallet 0xd8dA...6045</code>\n\n"
            "<i>You'll see total value, tokens held, risk scores, and you can tap any token for a full audit.</i>",
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
            "<b>/wallet &lt;address&gt;</b> — Portfolio audit\n"
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

            lines = [
                f"<b>{display_name} ({display_symbol})</b>",
                f"<code>{escape_html(address)}</code>",
                "",
                f"🎰 <b>AEGIS SCORE: {risk['score']:.1f}/10 \"{risk['label']}\"</b>",
                f"💰 <b>BAG ALERT:</b> {risk['bag_alert']}",
                f"🔍 <b>CODE:</b> {risk['code_summary']}",
                f"⏱️ <b>AGE:</b> {risk['age_str']} | <b>LIQ:</b> {risk['liq_str']} | <b>LP:</b> {risk['lp_status']}",
                f"🚨 <b>MAIN RISK:</b> {risk['main_risk']}",
            ]
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
            await msg.edit_text(reply, parse_mode="HTML")
        except Exception as e:
            await msg.edit_text(f"❌ Audit failed: {escape_html(str(e))}")
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
    elif data.startswith("audit_idx_"):
        idx = int(data.split("_")[-1]); tokens = context.user_data.get("wallet_tokens", [])
        if idx < len(tokens):
            token = tokens[idx]; token_address = token.get("token_address")
            token_chain = _get_token_chain(token).lower(); token_chain = "eth" if token_chain == "?" else token_chain
            token_name = token.get("token_name") or token.get("name") or token.get("symbol") or token_address[:10]
            token_symbol = token.get("token_symbol") or token.get("symbol") or ""
            await query.answer(f"Auditing {token_name}...")
            msg = await query.message.reply_text(f"🔍 Auditing <code>{escape_html(token_address)}</code> ({token_chain.upper()})...", parse_mode="HTML")
            try:
                contract, result = await run_scan(token_address, token_chain, config, fast_mode=True)
                score = result.get("risk_score","N/A"); rec = result.get("recommendation","?"); summary = result.get("summary","No summary.")
                display_name = contract.get("token_name") or token_name; display_symbol = contract.get("token_symbol") or token_symbol
                await msg.edit_text(f"<b>🛡️ Token Audit: {escape_html(display_name)}{(' ('+escape_html(display_symbol)+')') if display_symbol else ''}</b>\n<code>{escape_html(token_address)}</code> ({token_chain.upper()})\nRisk Score: {score}/10\nVerdict: <b>{escape_html(rec)}</b>\n\n📝 {escape_html(summary)}", parse_mode="HTML")
            except Exception as e: await msg.edit_text(f"❌ Audit failed: {escape_html(str(e))}")
    elif data.startswith("wallet_page_"):
        new_page = int(data.split("_")[-1]); context.user_data["wallet_page"] = new_page
        tokens = context.user_data.get("wallet_tokens", [])
        keyboard = _build_token_keyboard(tokens, page=new_page)
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await query.edit_message_text("I can explain that further. What specifically would you like to know?", parse_mode="HTML")

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
        ("verify", verify_command), ("status", status_command),
        ("degenflow", degenflow_command), ("flow", degenflow_command),
        ("new", degenflow_command), ("newtoken", degenflow_command),
        ("newtokens", degenflow_command), ("radar", degenflow_command),
        ("smartmoney", degenflow_command),
        ("scan", lambda u, c: scan_command(u, c, True)), ("deepscan", deepscan_command),
        ("deployer", deployer_command),
        ("wallet", wallet_command),
        ("trust", trust_command),
        ("compare", compare_command),
    ]:
        application.add_handler(CommandHandler(cmd, handler))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_error_handler(error_handler)

    # ── Manually initialise and start the bot (no run_polling) ─────────────
    logger.info("Starting Aegis Telegram Bot…")
    await application.initialize()
    await application.start()
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)

    # ── Run FastAPI in the same event loop ─────────────────────────────────
    port = int(os.getenv("PORT", "8000"))
    logger.info(f"Starting FastAPI on port {port}…")
    await run_fastapi_server(port)


if __name__ == "__main__":
    asyncio.run(main_async())
