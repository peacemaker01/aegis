# analysis/clone_detector.py
"""
Bytecode similarity / clone detection.
Extracts function selectors from runtime bytecode and computes
Jaccard similarity against known rug-pull templates.
"""
import re
from typing import List, Dict, Any, Optional
from utils.health import record_success, record_failure

# Known rug-pull contract selectors (from real scams)
RUGBY_SELECTORS = {
    "0x70a08231": "balanceOf",
    "0x18160ddd": "totalSupply",
    "0xdd62ed3e": "allowance",
    "0x095ea7b3": "approve",
    "0xa9059cbb": "transfer",
    "0x23b872dd": "transferFrom",
    "0x313ce567": "decimals",
    "0x06fdde03": "name",
    "0x95d89b41": "symbol",
    # Additional rug-specific selectors (from known scams)
    "0x3ccfd60b": "removeLimits",
    "0x5fe3b567": "setMaxTxAmount",
    "0x8f70ccf7": "setSellTax",
    "0x4a5e42e2": "enableTrading",
    "0x8456cb59": "pause",
    "0x3f4ba83a": "unpause",
    "0x715018a6": "renounceOwnership",
    "0xf2fde38b": "transferOwnership",
    "0x40c10f19": "mint",
    "0x42966c68": "burn",
    "0x79cc6790": "burnFrom",
    "0x4e6ec247": "setTax",
    "0x7f6d4c2e": "setFee",
    "0x8da5cb5b": "owner",
}

def extract_selectors_from_bytecode(bytecode: str) -> set:
    """
    Extract 4-byte function selectors from EVM runtime bytecode.
    Only works for verified contracts (we look for PUSH4 0xXXXXXXXX).
    """
    if not bytecode or bytecode.startswith("0x"):
        bytecode = bytecode[2:] if bytecode.startswith("0x") else bytecode
    selectors = set()
    # Pattern: PUSH4 (0x63) followed by 4 bytes
    pattern = re.compile(r'63([0-9a-f]{8})')
    matches = pattern.findall(bytecode)
    for m in matches:
        selectors.add("0x" + m)
    return selectors


def compute_similarity(selectors: set, template: set) -> float:
    """Jaccard similarity between two selector sets."""
    if not selectors or not template:
        return 0.0
    intersection = selectors.intersection(template)
    union = selectors.union(template)
    return len(intersection) / len(union) if union else 0.0


async def detect_clone(
    bytecode: str,
    debug: bool = False
) -> Dict[str, Any]:
    """
    Analyze bytecode for clone detection.
    Returns a dict with similarity score and flag.
    """
    try:
        selectors = extract_selectors_from_bytecode(bytecode)
        similarity = compute_similarity(selectors, set(RUGBY_SELECTORS.keys()))
        is_clone = similarity > 0.60  # threshold

        if debug:
            print(f"[DEBUG] Clone detection: {len(selectors)} selectors, similarity={similarity:.2f}")

        record_success("clone_detector")
        return {
            "similarity_score": round(similarity, 4),
            "is_clone": is_clone,
            "matched_selectors": list(selectors.intersection(set(RUGBY_SELECTORS.keys()))),
        }
    except Exception as e:
        record_failure("clone_detector", str(e))
        return {"similarity_score": 0.0, "is_clone": False, "matched_selectors": []}
