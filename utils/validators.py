import re

ETH_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

def is_valid_address(address: str) -> bool:
    return bool(ETH_ADDRESS_RE.match(address))
