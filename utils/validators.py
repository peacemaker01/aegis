# utils/validators.py
import re

ETH_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
# Solana base58 address pattern: 32-44 alphanumeric characters (excluding I, O, 0, l)
SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

def is_valid_address(address: str) -> bool:
    """Check if address is valid Ethereum or Solana format."""
    if not address:
        return False
    if ETH_ADDRESS_RE.match(address):
        return True
    if SOLANA_ADDRESS_RE.match(address):
        return True
    return False

def is_evm_address(address: str) -> bool:
    return bool(ETH_ADDRESS_RE.match(address))

def is_solana_address(address: str) -> bool:
    return bool(SOLANA_ADDRESS_RE.match(address))