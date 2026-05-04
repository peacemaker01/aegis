# analysis/goplus_check.py
"""
Layer 2: GoPlus Security API cross-reference.
Free, no API key required for basic checks.
Used as GROUND TRUTH for honeypot detection — binary, always accurate.
Endpoint: https://api.gopluslabs.io/api/v1/token_security/{chain_id}
"""
import httpx
from utils.rate_limiter import RateLimiter

goplus_limiter = RateLimiter(calls_per_second=2)

GOPLUS_CHAIN_IDS = {
    "eth": "1",
    "bsc": "56",
    "polygon": "137",
    "arb": "42161",
    "base": "8453",
    "op": "10",
    "avax": "43114",
    "fantom": "250",
    "solana": "solana",
}

GOPLUS_BASE = "https://api.gopluslabs.io/api/v1/token_security"


async def fetch_goplus(address: str, chain: str, debug: bool = False) -> dict:
    """
    Fetch GoPlus token security data.
    Returns raw GoPlus result dict, or empty dict on failure.
    """
    chain_id = GOPLUS_CHAIN_IDS.get(chain.lower())
    if not chain_id:
        if debug:
            print(f"[DEBUG] GoPlus: Unsupported chain {chain}")
        return {}

    await goplus_limiter.acquire()
    url = f"{GOPLUS_BASE}/{chain_id}"
    params = {"contract_addresses": address.lower()}

    if debug:
        print(f"[DEBUG] GoPlus request: {url} params={params}")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, params=params)
            if debug:
                print(f"[DEBUG] GoPlus response status: {r.status_code}")
            data = r.json()

        if data.get("code") != 1:
            if debug:
                print(f"[DEBUG] GoPlus API error: {data.get('message', 'unknown')}")
            return {}

        result = data.get("result", {})
        token_data = result.get(address.lower(), {})
        if debug:
            print(f"[DEBUG] GoPlus token data keys: {list(token_data.keys()) if token_data else 'empty'}")
        return token_data
    except Exception as e:
        if debug:
            print(f"[DEBUG] GoPlus exception: {type(e).__name__}: {e}")
        return {}


def parse_goplus(raw: dict) -> dict:
    """
    Parse GoPlus response into a clean, standardised dict.
    Maps GoPlus field names → Aegis field names.
    GoPlus uses "1" for true, "0" for false.
    Handles Solana-specific response structure gracefully.
    """
    if raw is None:
        return {"goplus_available": False}
    if not raw:
        return {"goplus_available": False}

    def _bool(key: str) -> bool:
        v = raw.get(key)
        if v is None:
            return False
        return str(v) == "1"

    def _float(key: str) -> float:
        v = raw.get(key)
        if v is None:
            return 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    def _str(key: str) -> str:
        v = raw.get(key)
        return str(v) if v is not None else ""

    return {
        "goplus_available": True,
        # Core honeypot / scam signals
        "gp_is_honeypot": _bool("is_honeypot"),
        "gp_is_open_source": _bool("is_open_source"),
        "gp_is_proxy": _bool("is_proxy"),
        "gp_is_mintable": _bool("is_mintable"),
        "gp_owner_change_balance": _bool("owner_change_balance"),
        "gp_can_be_minted": _bool("can_be_minted"),
        "gp_owner_address": _str("owner_address"),
        "gp_creator_address": _str("creator_address"),
        "gp_selfdestruct": _bool("selfdestruct"),
        "gp_external_call": _bool("external_call"),
        "gp_buy_tax": _float("buy_tax"),
        "gp_sell_tax": _float("sell_tax"),
        "gp_cannot_sell_all": _bool("cannot_sell_all"),
        "gp_cannot_buy": _bool("cannot_buy"),
        "gp_trading_cooldown": _bool("trading_cooldown"),
        "gp_transfer_pausable": _bool("transfer_pausable"),
        "gp_is_blacklisted": _bool("is_blacklisted"),
        "gp_is_whitelisted": _bool("is_whitelisted"),
        "gp_is_anti_whale": _bool("is_anti_whale"),
        "gp_anti_whale_modifiable": _bool("anti_whale_modifiable"),
        "gp_slippage_modifiable": _bool("slippage_modifiable"),
        "gp_hidden_owner": _bool("hidden_owner"),
        "gp_take_back_ownership": _bool("take_back_ownership"),
        "gp_owner_percent": _float("owner_percent"),
        "gp_creator_percent": _float("creator_percent"),
        "gp_holder_count": _str("holder_count"),
        "gp_lp_holder_count": _str("lp_holder_count"),
        "gp_total_supply": _str("total_supply"),
        "gp_token_name": _str("token_name"),
        "gp_token_symbol": _str("token_symbol"),
        "gp_dex": raw.get("dex", []),
    }


def goplus_risk_signals(parsed: dict) -> list[dict]:
    """
    Convert GoPlus data into standardised risk signals
    that can be merged with static + AI findings.
    """
    if not parsed.get("goplus_available"):
        return []

    signals = []

    def _flag(condition, severity, title, desc):
        if condition:
            signals.append({
                "severity": severity,
                "title": title,
                "description": desc,
                "source": "goplus"
            })

    _flag(parsed.get("gp_is_honeypot"),
          "CRITICAL", "GoPlus: Confirmed Honeypot",
          "GoPlus Security API confirms this token is a honeypot — you can buy but cannot sell.")

    _flag(parsed.get("gp_hidden_owner"),
          "CRITICAL", "GoPlus: Hidden Owner",
          "Contract has a hidden owner that can regain ownership at any time.")

    _flag(parsed.get("gp_take_back_ownership"),
          "CRITICAL", "GoPlus: Owner Can Reclaim Control",
          "Renounced ownership can be taken back by the original owner.")

    _flag(parsed.get("gp_cannot_sell_all"),
          "HIGH", "GoPlus: Cannot Sell All Tokens",
          "Sell restrictions detected — holders cannot sell 100% of their tokens.")

    _flag(parsed.get("gp_selfdestruct"),
          "HIGH", "GoPlus: Selfdestruct Present",
          "Contract contains a selfdestruct function.")

    buy_tax = parsed.get("gp_buy_tax") or 0
    sell_tax = parsed.get("gp_sell_tax") or 0

    if sell_tax and float(sell_tax) > 0.10:
        signals.append({
            "severity": "HIGH" if float(sell_tax) > 0.25 else "MEDIUM",
            "title": f"GoPlus: High Sell Tax ({float(sell_tax)*100:.0f}%)",
            "description": f"Sell tax is {float(sell_tax)*100:.0f}%. Trades are heavily penalised.",
            "source": "goplus",
        })

    owner_pct = parsed.get("gp_owner_percent") or 0
    if owner_pct and float(owner_pct) > 0.05:
        signals.append({
            "severity": "HIGH" if float(owner_pct) > 0.20 else "MEDIUM",
            "title": f"GoPlus: Owner Holds {float(owner_pct)*100:.1f}%",
            "description": "Owner holds a large % of supply — dump risk.",
            "source": "goplus",
        })

    return signals