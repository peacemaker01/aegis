# core/config.py
import os
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()


def load_config() -> dict:
    etherscan_raw = os.getenv("ETHERSCAN_API_KEY", "")
    infura_raw = os.getenv("INFURA_ID", "")
    helius_raw = os.getenv("HELIUS_API_KEY", "")
    rugcheck_raw = os.getenv("RUGCHECK_API_KEY", "")
    solsniffer_raw = os.getenv("SOLSNIFFER_API_KEY", "")
    birdeye_key = os.getenv("BIRDEYE_API_KEY", "")

    return {
        "openrouter": {
            "api_key": os.getenv("OPENROUTER_API_KEY", "").split(",")[0] if os.getenv("OPENROUTER_API_KEY") else "",
            "api_keys": [k.strip() for k in os.getenv("OPENROUTER_API_KEY", "").split(",") if k.strip()],
            "model": os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-r1"),
            "max_tokens": 4000,
            "temperature": 0.1,
        },
        "explorers": {
            "etherscan": [k.strip() for k in etherscan_raw.split(",") if k.strip()],
            "infura": [k.strip() for k in infura_raw.split(",") if k.strip()],
        },
        "preferences": {
            "default_chain": os.getenv("DEFAULT_CHAIN", "eth"),
            "stream_output": True,
            "deep_scan": os.getenv("DEEP_SCAN", "false").lower() == "true",
        },
        "notifications": {
            "telegram": {
                "bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
                "admin_user_id": int(os.getenv("ADMIN_USER_ID", "0")),
            }
        },
        "rpc": {
            "eth": os.getenv("RPC_ETH", ""),
            "bsc": os.getenv("RPC_BSC", "https://bsc-dataseed.binance.org/"),
            "polygon": os.getenv("RPC_POLYGON", ""),
            "arb": os.getenv("RPC_ARB", "https://arb1.arbitrum.io/rpc"),
            "base": os.getenv("RPC_BASE", "https://mainnet.base.org/"),
            "op": os.getenv("RPC_OP", "https://mainnet.optimism.io"),
            "avax": os.getenv("RPC_AVAX", "https://api.avax.network/ext/bc/C/rpc"),
            "fantom": os.getenv("RPC_FANTOM", "https://rpc.fantom.network/"),
            "zksync": os.getenv("RPC_ZKSYNC", "https://mainnet.era.zksync.io"),
            "gnosis": os.getenv("RPC_GNOSIS", "https://rpc.gnosischain.com/"),
            "solana": os.getenv("RPC_SOLANA", ""),
        },
        "solana": {
            "rpc_url": os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"),
            "helius_api_key": [k.strip() for k in helius_raw.split(",") if k.strip()],
            "rugcheck_api_key": [k.strip() for k in rugcheck_raw.split(",") if k.strip()],
            "solsniffer_api_key": [k.strip() for k in solsniffer_raw.split(",") if k.strip()],
            "payment_receiver_private_key": os.getenv("PAYMENT_RECEIVER_PRIVATE_KEY", ""),
            "treasury_wallet": os.getenv("TREASURY_WALLET", ""),
            "token_mint": os.getenv("TOKEN_MINT", "54YCMrqbdrPxiGYaByD1dPNJCWJN3R2zJ2jwPcGpump"),
            "fluxrpc_api_key": os.getenv("FLUXRPC_API_KEY", ""),
        },
        "subscription": {
            "price_usd": float(os.getenv("SUBSCRIPTION_PRICE_USD", "9.99")),
            "trial_days": int(os.getenv("TRIAL_DAYS", "3")),
            "subscription_days": int(os.getenv("SUBSCRIPTION_DAYS", "30")),
        },
        "telegram": {
            "bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
            "admin_user_id": int(os.getenv("ADMIN_USER_ID", "0")),
        },
        "database": {
            "path": os.getenv("DB_PATH", "aegis.db"),
        },
        "moralis": {
            "api_key": os.getenv("MORALIS_API_KEY", ""),
        },
        "redis": {
            "url": os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        },
        "webhook": {
            "base_url": os.getenv("WEBHOOK_BASE_URL", "http://localhost:8000"),
            "secret": os.getenv("MORALIS_WEBHOOK_SECRET", ""),
        },
        "birdeye": {
            "api_key": birdeye_key.split(",")[0] if birdeye_key else "",
            "api_keys": [k.strip() for k in birdeye_key.split(",") if k.strip()],
        },
        "cryptomus": {
            "merchant_id": os.getenv("CRYPTOMUS_MERCHANT_ID", ""),
            "payment_key": os.getenv("CRYPTOMUS_PAYMENT_KEY", ""),
        },
    }


def validate_config(config: dict) -> dict:
    errors = []
    warnings = []
    if not config["telegram"]["bot_token"]:
        errors.append("❌ TELEGRAM_BOT_TOKEN missing")
    if not config["openrouter"]["api_key"]:
        errors.append("❌ OPENROUTER_API_KEY missing")
    if not config["explorers"]["etherscan"]:
        warnings.append("⚠️ ETHERSCAN_API_KEY missing – EVM source fetch disabled")
    if not config["explorers"]["infura"]:
        warnings.append("⚠️ INFURA_ID missing – Mythril may fallback to public RPC")
    if not config["solana"]["helius_api_key"]:
        warnings.append("⚠️ HELIUS_API_KEY missing – Solana metadata fetch disabled")
    if not config["solana"]["payment_receiver_private_key"]:
        warnings.append("⚠️ PAYMENT_RECEIVER_PRIVATE_KEY missing – payment features disabled")
    if not config["solana"]["treasury_wallet"]:
        warnings.append("⚠️ TREASURY_WALLET missing – payment features disabled")
    if not config["moralis"]["api_key"]:
        warnings.append("⚠️ MORALIS_API_KEY missing – wallet tracking disabled")
    if not config["birdeye"]["api_keys"]:
        warnings.append("⚠️ BIRDEYE_API_KEY missing – Pump.fun Casino Feed disabled")
    if not config["cryptomus"]["merchant_id"] or not config["cryptomus"]["payment_key"]:
        warnings.append("⚠️ CRYPTOMUS keys missing – Subscription purchasing disabled")
    return {"errors": errors, "warnings": warnings}


config = load_config()
