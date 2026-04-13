import json, time
from pathlib import Path

CACHE_DIR = Path.home() / ".aegis" / "cache"

def _path(address: str, chain: str) -> Path:
    return CACHE_DIR / f"{chain}_{address.lower()}.json"

def get_cached(address: str, chain: str, ttl: int = 3600) -> dict | None:
    p = _path(address, chain)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        if time.time() - data.get("_cached_at", 0) < ttl:
            return data
    except Exception:
        pass
    return None

def set_cached(address: str, chain: str, data: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data["_cached_at"] = time.time()
    _path(address, chain).write_text(json.dumps(data, indent=2))

def clear_cache() -> int:
    if not CACHE_DIR.exists():
        return 0
    files = list(CACHE_DIR.glob("*.json"))
    for f in files:
        f.unlink()
    return len(files)
