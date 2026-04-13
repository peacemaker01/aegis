# core/watchlist.py
"""
Persistent watchlist manager.
Stores watched contracts in ~/.aegis/watchlist.json
"""
import json
from datetime import datetime, timezone
from pathlib import Path

WATCHLIST_FILE = Path.home() / ".aegis" / "watchlist.json"


def _load() -> dict:
    if not WATCHLIST_FILE.exists():
        return {"entries": {}}
    try:
        return json.loads(WATCHLIST_FILE.read_text())
    except Exception:
        return {"entries": {}}


def _save(data: dict) -> None:
    WATCHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST_FILE.write_text(json.dumps(data, indent=2))


def _key(address: str, chain: str) -> str:
    return f"{chain}_{address.lower()}"


# ── Public API ────────────────────────────────────────────────────────────────

def add_entry(
    address: str,
    chain: str,
    label: str = "",
    alert_threshold: float = 6.0,
) -> dict:
    """Add a contract to the watchlist. Returns the entry."""
    data = _load()
    k    = _key(address, chain)
    if k in data["entries"]:
        return data["entries"][k]          # already exists

    entry = {
        "address":         address.lower(),
        "chain":           chain,
        "label":           label or address[:10] + "…",
        "added":           datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
        "last_checked":    None,
        "last_risk_score": None,
        "last_verdict":    None,
        "alert_threshold": alert_threshold,
        "active":          True,
    }
    data["entries"][k] = entry
    _save(data)
    return entry


def remove_entry(address: str, chain: str) -> bool:
    data = _load()
    k    = _key(address, chain)
    if k not in data["entries"]:
        return False
    del data["entries"][k]
    _save(data)
    return True


def list_entries(active_only: bool = True) -> list[dict]:
    data = _load()
    entries = list(data["entries"].values())
    if active_only:
        entries = [e for e in entries if e.get("active", True)]
    return sorted(entries, key=lambda x: x.get("chain", "") + x.get("address", ""))


def update_entry(address: str, chain: str, **kwargs) -> None:
    """Update fields on an existing entry (e.g. last_risk_score)."""
    data = _load()
    k    = _key(address, chain)
    if k in data["entries"]:
        data["entries"][k].update(kwargs)
        _save(data)


def get_entry(address: str, chain: str) -> dict | None:
    data = _load()
    return data["entries"].get(_key(address, chain))


def count() -> int:
    return len(_load()["entries"])
