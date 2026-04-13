# analysis/static_checks.py
"""
Layer 1: Deterministic static analysis.
Runs BEFORE the AI — catches known patterns with 100% consistency.
No hallucinations. No inconsistency. Always the same result.
"""
import re
from dataclasses import dataclass, field


@dataclass
class StaticFinding:
    severity:   str
    title:      str
    description:str
    pattern:    str
    confidence: str = "HIGH"


@dataclass
class StaticResult:
    has_mint_function:         bool = False
    has_blacklist:             bool = False
    has_max_tx_limit:          bool = False
    has_fee_modification:      bool = False
    has_selfdestruct:          bool = False
    has_delegatecall:          bool = False
    has_reentrancy_risk:       bool = False
    has_tx_origin:             bool = False
    has_emergency_withdraw:    bool = False
    has_pausable:              bool = False
    has_proxy_pattern:         bool = False
    has_unchecked_transfer:    bool = False
    has_integer_overflow_risk: bool = False
    uses_openzeppelin:         bool = False
    has_timelock:              bool = False
    owner_renounced_pattern:   bool = False
    findings: list = field(default_factory=list)
    static_risk_score: float = 0.0
    confidence: str = "HIGH"


PATTERNS = {
    "mint": (
        re.compile(r"function\s+mint\s*\([^)]*\).*?(?:external|public)", re.DOTALL|re.IGNORECASE),
        "CRITICAL","Mint Function Detected",
        "Contract has a mint function. Owner can create unlimited tokens and dilute supply.",
    ),
    "blacklist": (
        re.compile(r"(?:blacklist|blocklist|banned|isBlocked|_blacklisted)\s*[\[\(]", re.IGNORECASE),
        "HIGH","Blacklist Function Detected",
        "Contract can blacklist addresses, preventing selling — a classic honeypot mechanism.",
    ),
    "max_tx": (
        re.compile(r"(?:maxTxAmount|_maxTxAmount|maxTransactionAmount|maxTx)\s*[=;]", re.IGNORECASE),
        "MEDIUM","Max Transaction Limit",
        "Transfer amounts capped. Owner may lower this to prevent large sell orders.",
    ),
    "fee_mod": (
        re.compile(r"function\s+set(?:Fee|Tax|Rate|BuyFee|SellFee|BuyTax|SellTax)\s*\([^)]*\)", re.IGNORECASE),
        "HIGH","Modifiable Transfer Tax",
        "Owner can change fees after deployment — can be set to 100% to trap funds.",
    ),
    "selfdestruct": (
        re.compile(r"\bselfdestruct\b|\bsuicide\b", re.IGNORECASE),
        "CRITICAL","Self-Destruct Function",
        "Contract can self-destruct, destroying all state and retrieving ETH.",
    ),
    "delegatecall": (
        re.compile(r"\bdelegatecall\b", re.IGNORECASE),
        "HIGH","Delegatecall Usage",
        "Delegatecall allows arbitrary code execution in contract context if not guarded.",
    ),
    "reentrancy": (
        re.compile(r"\.call\{value\s*:.*?\}\(|\.call\.value\(", re.IGNORECASE),
        "HIGH","Potential Reentrancy",
        "Low-level .call with value — verify state updates before external calls.",
    ),
    "tx_origin": (
        re.compile(r"\btx\.origin\b", re.IGNORECASE),
        "MEDIUM","tx.origin Authentication",
        "tx.origin auth is phishing-vulnerable. Use msg.sender instead.",
    ),
    "emergency_withdraw": (
        re.compile(r"function\s+(?:emergencyWithdraw|withdrawAll|drain|rescue)\s*\([^)]*\)", re.IGNORECASE),
        "HIGH","Emergency Withdraw",
        "Owner can withdraw all funds from the contract.",
    ),
    "pausable": (
        re.compile(r"\bPausable\b|\b_paused\b|\bwhenNotPaused\b", re.IGNORECASE),
        "MEDIUM","Pausable Contract",
        "Owner can pause all transfers — can prevent selling.",
    ),
    "proxy": (
        re.compile(r"(?:TransparentUpgradeableProxy|UUPSUpgradeable|ERC1967Proxy|_implementation\(\))", re.IGNORECASE),
        "MEDIUM","Upgradeable Proxy",
        "Contract is upgradeable — owner can replace code post-deployment.",
    ),
    "unchecked_transfer": (
        re.compile(r"\.transfer\(|\.send\(", re.IGNORECASE),
        "LOW","Unchecked Transfer",
        "Use SafeERC20 instead of .transfer() or .send().",
    ),
    "overflow": (
        re.compile(r"pragma solidity\s+\^?0\.[0-7]\.", re.IGNORECASE),
        "MEDIUM","Old Solidity — Overflow Risk",
        "Solidity < 0.8.0 lacks built-in overflow protection — SafeMath required.",
    ),
    "openzeppelin": (
        re.compile(r"@openzeppelin|openzeppelin/contracts", re.IGNORECASE),
        "INFO","Uses OpenZeppelin",
        "Imports from OpenZeppelin — well-audited library, positive signal.",
    ),
    "timelock": (
        re.compile(r"\bTimelockController\b|\btimelock\b", re.IGNORECASE),
        "INFO","Timelock Present",
        "Admin actions are time-locked — strong positive governance signal.",
    ),
    "renounce": (
        re.compile(r"renounceOwnership\(\)", re.IGNORECASE),
        "INFO","Renounce Ownership Present",
        "Contract can renounce ownership. Verify on-chain if it has been called.",
    ),
}

SCORE_WEIGHTS = {
    "CRITICAL": 3.0, "HIGH": 1.5, "MEDIUM": 0.7,
    "LOW": 0.2,      "INFO": -0.3,
}

FLAG_MAP = {
    "mint": "has_mint_function", "blacklist": "has_blacklist",
    "max_tx": "has_max_tx_limit", "fee_mod": "has_fee_modification",
    "selfdestruct": "has_selfdestruct", "delegatecall": "has_delegatecall",
    "reentrancy": "has_reentrancy_risk", "tx_origin": "has_tx_origin",
    "emergency_withdraw": "has_emergency_withdraw", "pausable": "has_pausable",
    "proxy": "has_proxy_pattern", "unchecked_transfer": "has_unchecked_transfer",
    "overflow": "has_integer_overflow_risk", "openzeppelin": "uses_openzeppelin",
    "timelock": "has_timelock", "renounce": "owner_renounced_pattern",
}


def run_static_checks(source: str) -> StaticResult:
    result = StaticResult()
    if not source or not source.strip():
        result.confidence = "LOW"
        return result

    score = 0.0
    for key, (pattern, severity, title, description) in PATTERNS.items():
        if pattern.search(source):
            result.findings.append(StaticFinding(severity, title, description, key))
            score += SCORE_WEIGHTS.get(severity, 0)
            attr = FLAG_MAP.get(key)
            if attr:
                setattr(result, attr, True)

    result.static_risk_score = min(10.0, max(0.0, score * 1.2))
    return result


def static_to_dict(r: StaticResult) -> dict:
    return {
        "static_checks": {
            k: getattr(r, k) for k in FLAG_MAP.values()
        },
        "static_risk_score": round(r.static_risk_score, 2),
        "static_findings": [
            {"severity": f.severity, "title": f.title,
             "description": f.description, "source": "static_analysis"}
            for f in r.findings
        ],
        "static_confidence": r.confidence,
    }
