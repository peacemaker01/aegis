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
            CREATE TABLE IF NOT EXISTS usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                command TEXT,
                address TEXT,
                chain TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

async def log_usage(user_id: int, command: str, address: str = "", chain: str = "") -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO usage_logs (user_id, command, address, chain)
            VALUES (?, ?, ?, ?)
        ''', (user_id, command, address, chain))
        await db.commit()

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