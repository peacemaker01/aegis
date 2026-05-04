# core/chains.py
# Etherscan V2 — single API key covers all EVM chains
# Solana uses separate RPC/API infrastructure

CHAINS = {
    "eth":     {"id": 1,      "name": "Ethereum",       "symbol": "ETH"},
    "bsc":     {"id": 56,     "name": "BNB Smart Chain", "symbol": "BNB"},
    "polygon": {"id": 137,    "name": "Polygon",         "symbol": "MATIC"},
    "arb":     {"id": 42161,  "name": "Arbitrum One",    "symbol": "ETH"},
    "base":    {"id": 8453,   "name": "Base",            "symbol": "ETH"},
    "op":      {"id": 10,     "name": "Optimism",        "symbol": "ETH"},
    "avax":    {"id": 43114,  "name": "Avalanche",       "symbol": "AVAX"},
    "fantom":  {"id": 250,    "name": "Fantom",          "symbol": "FTM"},
    "zksync":  {"id": 324,    "name": "zkSync Era",      "symbol": "ETH"},
    "gnosis":  {"id": 100,    "name": "Gnosis",          "symbol": "xDAI"},
    "solana":  {"id": None,   "name": "Solana",          "symbol": "SOL"},
}

CHAIN_ALIASES = {
    "ethereum": "eth",
    "bnb":      "bsc",
    "binance":  "bsc",
    "matic":    "polygon",
    "arbitrum": "arb",
    "optimism": "op",
    "avalanche":"avax",
    "ftm":      "fantom",
}

ETHERSCAN_BASE = "https://api.etherscan.io/v2/api"


def resolve_chain(name: str) -> str:
    """Normalise any chain name/alias to the canonical key."""
    key = name.lower().strip()
    return CHAIN_ALIASES.get(key, key)


def get_chain(name: str) -> dict:
    """Return chain metadata dict with canonical key."""
    key = resolve_chain(name)
    if key not in CHAINS:
        supported = ", ".join(CHAINS.keys())
        raise ValueError(f"Unsupported chain '{name}'. Supported: {supported}")
    return {"key": key, **CHAINS[key]}