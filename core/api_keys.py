# core/api_keys.py
"""
Built-in API keys for Aegis.

Keys are XOR-obfuscated to prevent trivial extraction via `strings` on the binary.
This is NOT cryptographic security — it's just casual obfuscation.

To generate obfuscated keys, run:
    python -c "from core.api_keys import obfuscate; print(obfuscate('your-real-api-key-here'))"

Then paste the output into _OPENROUTER_ENC or _ETHERSCAN_ENC below.
"""
import base64

_OBF_KEY = b"aegis-shield-2026"


def _deobfuscate(encoded: str) -> str:
    """Decode an XOR-obfuscated API key."""
    if not encoded:
        return ""
    try:
        raw = base64.b64decode(encoded)
        result = bytes(b ^ _OBF_KEY[i % len(_OBF_KEY)] for i, b in enumerate(raw))
        return result.decode("utf-8")
    except Exception:
        return ""


def obfuscate(plaintext: str) -> str:
    """
    Encode a plaintext API key for embedding in this file.

    Usage (run once per key, paste result below):
        python -c "from core.api_keys import obfuscate; print(obfuscate('sk-or-v1-xxxx'))"
    """
    raw = plaintext.encode("utf-8")
    result = bytes(b ^ _OBF_KEY[i % len(_OBF_KEY)] for i, b in enumerate(raw))
    return base64.b64encode(result).decode("utf-8")


# ── Obfuscated API Keys ──────────────────────────────────────────────────────
# IMPORTANT: Replace these with your real obfuscated keys before building.
#
# Step 1: Get your API keys from:
#   - OpenRouter: https://openrouter.ai/keys
#   - Etherscan:  https://etherscan.io/apis
#
# Step 2: Obfuscate each key:
#   python -c "from core.api_keys import obfuscate; print(obfuscate('sk-or-v1-your-key'))"
#   python -c "from core.api_keys import obfuscate; print(obfuscate('YOUR_ETHERSCAN_KEY'))"
#
# Step 3: Paste the base64 output below:

_OPENROUTER_ENC = ""   # ← paste obfuscated OpenRouter API key here
_ETHERSCAN_ENC  = ""   # ← paste obfuscated Etherscan V2 API key here


def get_openrouter_key() -> str:
    """Return the built-in OpenRouter API key."""
    return _deobfuscate(_OPENROUTER_ENC)


def get_etherscan_key() -> str:
    """Return the built-in Etherscan V2 API key."""
    return _deobfuscate(_ETHERSCAN_ENC)
