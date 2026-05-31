# core/db.py
import aiosqlite
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

DB_PATH = "aegis.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Existing tables
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                trial_ends_at TIMESTAMP,
                subscription_expires_at TIMESTAMP,
                wallet_address TEXT,
                openrouter_key TEXT,
                credits INTEGER DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS subscription_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                tx_signature TEXT UNIQUE,
                amount_tokens REAL,
                usd_value REAL,
                burn_amount REAL,
                treasury_amount REAL,
                split_tx_signature TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS used_signatures (
                signature TEXT PRIMARY KEY,
                used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS cryptomus_orders (
                order_id TEXT PRIMARY KEY,
                user_id INTEGER,
                amount_usd REAL,
                days INTEGER,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                command TEXT,
                address TEXT,
                chain TEXT,
                risk_score REAL,
                verdict TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS group_subscriptions (
                group_id INTEGER PRIMARY KEY,
                owner_user_id INTEGER NOT NULL,
                subscription_expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                api_key TEXT UNIQUE NOT NULL,
                label TEXT DEFAULT '',
                active INTEGER DEFAULT 1,
                requests_today INTEGER DEFAULT 0,
                daily_limit INTEGER DEFAULT 500,
                last_reset_date TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_alert_filters (
                user_id INTEGER PRIMARY KEY,
                min_liq_usd REAL DEFAULT 0,
                max_top10_pct REAL DEFAULT 100,
                require_lp_locked INTEGER DEFAULT 0,
                require_clean_deployer INTEGER DEFAULT 0,
                max_risk_score REAL DEFAULT 10.0,
                chains TEXT DEFAULT 'solana',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # New tables for whale tracking
        await db.execute('''
            CREATE TABLE IF NOT EXISTS watched_wallets (
                id TEXT PRIMARY KEY,
                address TEXT NOT NULL,
                label TEXT,
                stream_id TEXT,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                min_value_usd REAL DEFAULT 50000.0,
                active INTEGER DEFAULT 1,
                channels TEXT DEFAULT 'telegram',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS token_watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                address TEXT NOT NULL,
                chain TEXT NOT NULL,
                label TEXT DEFAULT '',
                token_name TEXT DEFAULT '',
                token_symbol TEXT DEFAULT '',
                alert_threshold REAL DEFAULT 6.0,
                last_risk_score REAL,
                last_verdict TEXT,
                last_top10_pct REAL,
                last_holder_count INTEGER,
                last_liq_usd REAL,
                last_checked TIMESTAMP,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                active INTEGER DEFAULT 1,
                UNIQUE(user_id, address, chain)
            )
        ''')
        await db.commit()

# ---------- existing user / subscription functions unchanged ----------
async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def create_user(user_id: int, username: str = "", first_name: str = "", trial_days: int = 3) -> None:
    trial_ends = datetime.now(timezone.utc) + timedelta(days=trial_days)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name, trial_ends_at)
            VALUES (?, ?, ?, ?)
        ''', (user_id, username, first_name, trial_ends.isoformat()))
        await db.commit()

async def update_user_subscription(user_id: int, expires_at: datetime, wallet_address: str = "") -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            UPDATE users SET subscription_expires_at = ?, wallet_address = ?
            WHERE user_id = ?
        ''', (expires_at.isoformat(), wallet_address, user_id))
        await db.commit()

async def is_signature_used(signature: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM used_signatures WHERE signature = ?", (signature,)) as cursor:
            return await cursor.fetchone() is not None

async def mark_signature_used(signature: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO used_signatures (signature) VALUES (?)", (signature,))
        await db.commit()

async def log_subscription_event(
    user_id: int,
    tx_signature: str,
    amount_tokens: float,
    usd_value: float,
    burn_amount: float,
    treasury_amount: float,
    split_tx_signature: str = ""
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO subscription_events
            (user_id, tx_signature, amount_tokens, usd_value, burn_amount, treasury_amount, split_tx_signature)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, tx_signature, amount_tokens, usd_value, burn_amount, treasury_amount, split_tx_signature))
        await db.commit()

async def log_usage(user_id: int, command: str, address: str = "", chain: str = "",
                    risk_score: float = None, verdict: str = "") -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO usage_logs (user_id, command, address, chain, risk_score, verdict)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, command, address, chain, risk_score, verdict))
        await db.commit()


async def get_scan_history(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT command, address, chain, risk_score, verdict, created_at
            FROM usage_logs
            WHERE user_id = ? AND address != '' AND command IN ('scan','deepscan','audit')
            ORDER BY created_at DESC LIMIT ?
        ''', (user_id, limit)) as cur:
            return [dict(r) for r in await cur.fetchall()]

# ---------- New functions for whale tracking ----------
async def add_watched_wallet(user_id: int, address: str, label: str = None, stream_id: str = None) -> str:
    """Add a wallet to the watchlist. Returns the generated ID."""
    import uuid
    wallet_id = str(uuid.uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO watched_wallets (id, address, label, stream_id, user_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (wallet_id, address.lower(), label, stream_id, user_id))
        await db.commit()
    return wallet_id

async def remove_watched_wallet(user_id: int, address: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('''
            DELETE FROM watched_wallets WHERE user_id = ? AND address = ?
        ''', (user_id, address.lower()))
        await db.commit()
        return cursor.rowcount > 0

async def get_watched_wallet_by_address(user_id: int, address: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM watched_wallets WHERE user_id = ? AND address = ?",
            (user_id, address.lower())
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def get_watched_wallets(user_id: int) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM watched_wallets WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def add_alert(user_id: int, tag: str, min_value_usd: float = 50000.0, channels: str = "telegram") -> str:
    import uuid
    alert_id = str(uuid.uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO alerts (id, user_id, tag, min_value_usd, active, channels)
            VALUES (?, ?, ?, ?, 1, ?)
        ''', (alert_id, user_id, tag, min_value_usd, channels))
        await db.commit()
    return alert_id

async def get_alerts_by_tag(tag: str) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM alerts WHERE tag = ? AND active = 1",
            (tag,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def get_alerts_by_user(user_id: int) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM alerts WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def update_alert_threshold(user_id: int, tag: str, min_value_usd: float) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute('''
            UPDATE alerts SET min_value_usd = ? WHERE user_id = ? AND tag = ?
        ''', (min_value_usd, user_id, tag))
        await db.commit()
        return cursor.rowcount > 0

async def toggle_alert(user_id: int, tag: str) -> Optional[bool]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT active FROM alerts WHERE user_id = ? AND tag = ?",
            (user_id, tag)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            new_state = 0 if row[0] else 1
            await db.execute(
                "UPDATE alerts SET active = ? WHERE user_id = ? AND tag = ?",
                (new_state, user_id, tag)
            )
            await db.commit()
            return bool(new_state)

async def create_cryptomus_order(order_id: str, user_id: int, amount_usd: float, days: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO cryptomus_orders (order_id, user_id, amount_usd, days, status)
            VALUES (?, ?, ?, ?, 'pending')
        ''', (order_id, user_id, amount_usd, days))
        await db.commit()

async def get_cryptomus_order(order_id: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM cryptomus_orders WHERE order_id = ?", (order_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def update_cryptomus_order_status(order_id: str, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE cryptomus_orders SET status = ? WHERE order_id = ?", (status, order_id))
        await db.commit()

# ── Token Watchlist (per-user contract monitoring) ────────────────────────────

async def watchlist_add(
    user_id: int, address: str, chain: str,
    label: str = "", token_name: str = "", token_symbol: str = "",
    alert_threshold: float = 6.0,
) -> bool:
    """Add a token to a user's watchlist. Returns True if newly added."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            INSERT OR IGNORE INTO token_watchlist
                (user_id, address, chain, label, token_name, token_symbol, alert_threshold)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, address.lower(), chain.lower(), label, token_name, token_symbol, alert_threshold))
        await db.commit()
        return cursor.rowcount > 0


async def watchlist_remove(user_id: int, address: str) -> bool:
    """Remove / deactivate all entries for this address across chains."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE token_watchlist SET active=0 WHERE user_id=? AND address=?",
            (user_id, address.lower())
        )
        await db.commit()
        return cursor.rowcount > 0


async def watchlist_list(user_id: int) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM token_watchlist WHERE user_id=? AND active=1 ORDER BY added_at DESC",
            (user_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def watchlist_get_all_active() -> List[Dict[str, Any]]:
    """Fetch every active entry across all users — used by the monitor loop."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM token_watchlist WHERE active=1 ORDER BY user_id, added_at"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def watchlist_update_state(
    user_id: int, address: str, chain: str,
    risk_score: float, verdict: str,
    top10_pct: Optional[float] = None,
    holder_count: Optional[int] = None,
    liq_usd: Optional[float] = None,
) -> None:
    """Persist the latest scan state for change-detection next cycle."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE token_watchlist
            SET last_risk_score=?, last_verdict=?, last_top10_pct=?,
                last_holder_count=?, last_liq_usd=?, last_checked=?
            WHERE user_id=? AND address=? AND chain=?
        """, (risk_score, verdict, top10_pct, holder_count, liq_usd, now,
              user_id, address.lower(), chain.lower()))
        await db.commit()

# ── Group Subscriptions ───────────────────────────────────────────────────────

async def get_group_subscription(group_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM group_subscriptions WHERE group_id=?", (group_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def set_group_subscription(group_id: int, owner_user_id: int, expires_at: datetime) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO group_subscriptions (group_id, owner_user_id, subscription_expires_at)
            VALUES (?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                owner_user_id=excluded.owner_user_id,
                subscription_expires_at=excluded.subscription_expires_at
        """, (group_id, owner_user_id, expires_at.isoformat()))
        await db.commit()

async def group_subscription_active(group_id: int) -> bool:
    sub = await get_group_subscription(group_id)
    if not sub or not sub.get("subscription_expires_at"):
        return False
    exp = datetime.fromisoformat(sub["subscription_expires_at"])
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp > datetime.now(timezone.utc)


# ── API Keys ──────────────────────────────────────────────────────────────────

async def create_api_key(user_id: int, label: str = "") -> str:
    import secrets
    key = "aegis_" + secrets.token_urlsafe(32)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO api_keys (user_id, api_key, label, last_reset_date) VALUES (?, ?, ?, ?)",
            (user_id, key, label, today)
        )
        await db.commit()
    return key

async def get_api_key(api_key: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM api_keys WHERE api_key=? AND active=1", (api_key,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def get_user_api_keys(user_id: int) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM api_keys WHERE user_id=? AND active=1 ORDER BY created_at DESC", (user_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

async def revoke_api_key(user_id: int, api_key: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE api_keys SET active=0 WHERE user_id=? AND api_key=?", (user_id, api_key)
        )
        await db.commit()
        return cur.rowcount > 0

async def check_api_key_limit(api_key: str) -> bool:
    """Returns True if the key is within its daily limit. Resets counter at midnight."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM api_keys WHERE api_key=? AND active=1", (api_key,)) as cur:
            row = await cur.fetchone()
        if not row:
            return False
        row = dict(row)
        if row.get("last_reset_date") != today:
            await db.execute(
                "UPDATE api_keys SET requests_today=0, last_reset_date=? WHERE api_key=?",
                (today, api_key)
            )
            row["requests_today"] = 0
        if row["requests_today"] >= row["daily_limit"]:
            return False
        await db.execute(
            "UPDATE api_keys SET requests_today=requests_today+1 WHERE api_key=?", (api_key,)
        )
        await db.commit()
    return True


# ── User Alert Filters ────────────────────────────────────────────────────────

async def get_alert_filters(user_id: int) -> Dict[str, Any]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM user_alert_filters WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
            if row:
                return dict(row)
    # Return defaults
    return {
        "user_id": user_id, "min_liq_usd": 0, "max_top10_pct": 100,
        "require_lp_locked": 0, "require_clean_deployer": 0,
        "max_risk_score": 10.0, "chains": "solana",
    }

async def set_alert_filters(user_id: int, **kwargs) -> None:
    now = datetime.now(timezone.utc).isoformat()
    existing = await get_alert_filters(user_id)
    existing.update(kwargs)
    existing["updated_at"] = now
    existing.pop("user_id", None)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO user_alert_filters
                (user_id, min_liq_usd, max_top10_pct, require_lp_locked,
                 require_clean_deployer, max_risk_score, chains, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                min_liq_usd=excluded.min_liq_usd,
                max_top10_pct=excluded.max_top10_pct,
                require_lp_locked=excluded.require_lp_locked,
                require_clean_deployer=excluded.require_clean_deployer,
                max_risk_score=excluded.max_risk_score,
                chains=excluded.chains,
                updated_at=excluded.updated_at
        """, (user_id, existing["min_liq_usd"], existing["max_top10_pct"],
              existing["require_lp_locked"], existing["require_clean_deployer"],
              existing["max_risk_score"], existing["chains"], now))
        await db.commit()
