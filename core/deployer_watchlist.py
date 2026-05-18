# core/deployer_watchlist.py
"""
Persistent watchlist for Deployer addresses.
Used for real-time alerting on new deployments.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

WATCHLIST_FILE = Path.home() / ".aegis" / "deployer_watchlist.json"

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

def add_deployer(
    address: str,
    chain: str,
    label: str = "",
    user_id: Optional[int] = None,
) -> dict:
    """Add a deployer to the watchlist."""
    data = _load()
    addr_lower = address.lower()
    key = f"{chain}_{addr_lower}"
    
    if key in data["entries"]:
        return data["entries"][key]

    entry = {
        "address": addr_lower,
        "chain": chain,
        "label": label or f"Deployer {address[:8]}",
        "user_id": user_id,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "last_known_tx": None,
        "active": True
    }
    data["entries"][key] = entry
    _save(data)
    return entry

def remove_deployer(address: str, chain: str) -> bool:
    data = _load()
    key = f"{chain}_{address.lower()}"
    if key in data["entries"]:
        del data["entries"][key]
        _save(data)
        return True
    return False

def list_watched_deployers() -> List[Dict]:
    data = _load()
    return list(data["entries"].values())

def update_deployer(address: str, chain: str, **kwargs) -> None:
    data = _load()
    key = f"{chain}_{address.lower()}"
    if key in data["entries"]:
        data["entries"][key].update(kwargs)
        _save(data)
