# core/group_subscription.py
"""
Group/community bot subscription logic.

A group admin can activate a group subscription via /groupsub.
All members of that group can then use scan commands without individual subs.
The group subscription is tied to the activating user's payment — they pay
once for the whole group. Priced at the same $79/month or $699/year.

Behaviour:
  - In a group chat: check group subscription first, then fall back to
    checking if the individual user has a personal subscription.
  - Group scans are rate-capped at 200 scans/day to prevent abuse.
  - Sensitive commands (/subscribe, /status, /verify) remain private-only.
"""
from datetime import datetime, timezone
from core.db import (
    group_subscription_active, get_group_subscription,
    set_group_subscription, DB_PATH
)
import aiosqlite

# Daily scan cap per group
GROUP_DAILY_SCAN_CAP = 200


async def is_group_scan_allowed(group_id: int) -> bool:
    """True if the group has an active subscription."""
    return await group_subscription_active(group_id)


async def get_group_scan_count_today(group_id: int) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT COUNT(*) FROM usage_logs
            WHERE address LIKE ? AND created_at >= ?
        """, (f"%group_{group_id}%", today + " 00:00:00")) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def group_within_cap(group_id: int) -> bool:
    count = await get_group_scan_count_today(group_id)
    return count < GROUP_DAILY_SCAN_CAP


def format_group_status(sub: dict) -> str:
    """Format group subscription status for display."""
    if not sub:
        return (
            "❌ <b>No Group Subscription</b>\n\n"
            "Activate with /groupsub to allow all group members to use Aegis.\n"
            "Pricing: $79/month or $699/year (same as personal)."
        )
    exp_str = sub.get("subscription_expires_at", "")[:10]
    now = datetime.now(timezone.utc)
    try:
        exp = datetime.fromisoformat(sub["subscription_expires_at"])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        days_left = (exp - now).days
        status = f"✅ Active — expires {exp_str} ({days_left}d remaining)"
    except Exception:
        status = "⚠️ Unknown expiry"
    return (
        f"👥 <b>Group Subscription</b>\n\n"
        f"Status: {status}\n"
        f"Daily scan cap: {GROUP_DAILY_SCAN_CAP} scans/day\n\n"
        f"All group members can use /scan, /deepscan, /deployer, and /watchlist."
    )