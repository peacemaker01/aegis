# analysis/schema.py
"""
Layer 3: Pydantic schema validation for all AI responses.
Every AI output is validated — malformed JSON triggers automatic retry.
Inconsistent fields are corrected against ground truth from static + GoPlus.
"""
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional


class AuditFinding(BaseModel):
    severity:    str
    title:       str
    description: str
    code_ref:    Optional[str] = None

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v):
        allowed = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
        return v.upper() if v.upper() in allowed else "INFO"


class AuditResult(BaseModel):
    risk_score:               float = Field(..., ge=0.0, le=10.0)
    recommendation:           str   = "CAUTION"
    honeypot:                 bool  = False
    mint_function:            bool  = False
    owner_renounced:          bool  = False
    proxy_pattern:            bool  = False
    hidden_owner:             bool  = False
    transfer_tax_modifiable:  bool  = False
    blacklist_function:       bool  = False
    max_tx_limit:             bool  = False
    liquidity_concerns:       bool  = False
    findings:                 list[AuditFinding] = []
    positive_signals:         list[str]          = []
    summary:                  str  = ""
    audit_cost_equivalent:    str  = "$8,000-$20,000"

    @field_validator("recommendation")
    @classmethod
    def validate_rec(cls, v):
        allowed = {"SAFE", "CAUTION", "AVOID"}
        return v.upper() if v.upper() in allowed else "CAUTION"

    @model_validator(mode="before")
    @classmethod
    def pre_clamp(cls, data: dict) -> dict:
        """Clamp risk_score BEFORE field validation (Field ge/le)."""
        if isinstance(data, dict) and "risk_score" in data:
            try:
                data["risk_score"] = round(max(0.0, min(10.0, float(data["risk_score"]))), 1)
            except (TypeError, ValueError):
                data["risk_score"] = 5.0
        return data

    @model_validator(mode="after")
    def consistency_check(self):
        """Auto-correct obvious internal inconsistencies."""
        # If honeypot=True, score must be at least 8.0
        if self.honeypot and self.risk_score < 8.0:
            self.risk_score = max(self.risk_score, 8.0)
        # Recommendation must match score
        if self.risk_score >= 7.0 and self.recommendation == "SAFE":
            self.recommendation = "AVOID"
        if self.risk_score <= 3.0 and self.recommendation == "AVOID":
            self.recommendation = "CAUTION"
        return self


class DeployerResult(BaseModel):
    risk_score:              float = Field(..., ge=0.0, le=10.0)
    verdict:                 str   = "SUSPICIOUS"
    pattern:                 str   = ""
    findings:                list[AuditFinding] = []
    chain_hopping:           bool  = False
    identity_obfuscation:    bool  = False
    reuse_pattern:           bool  = False
    estimated_victims:       Optional[int] = None
    total_contracts_deployed:int   = 0
    red_flags:               list[str] = []
    summary:                 str  = ""
    recommendation:          str  = "CAUTION"

    @field_validator("verdict")
    @classmethod
    def validate_verdict(cls, v):
        allowed = {"CLEAN", "SUSPICIOUS", "KNOWN_RUGGER", "SERIAL_RUGGER"}
        return v.upper() if v.upper() in allowed else "SUSPICIOUS"

    @field_validator("recommendation")
    @classmethod
    def validate_rec(cls, v):
        allowed = {"TRUST", "CAUTION", "AVOID", "BLACKLIST"}
        return v.upper() if v.upper() in allowed else "CAUTION"


class MonitorResult(BaseModel):
    alert:       bool = False
    alert_level: str  = "NONE"
    changes:     list[str] = []
    new_risk_score: float = 0.0
    old_risk_score: float = 0.0
    message:     str  = ""
    action:      str  = "NONE"

    @field_validator("alert_level")
    @classmethod
    def validate_level(cls, v):
        return v.upper() if v.upper() in {"NONE","INFO","WARNING","CRITICAL"} else "NONE"

    @field_validator("action")
    @classmethod
    def validate_action(cls, v):
        return v.upper() if v.upper() in {"NONE","REVIEW","SELL_IMMEDIATELY"} else "NONE"


def validate_audit(raw: dict) -> AuditResult:
    return AuditResult.model_validate(raw)

def validate_deployer(raw: dict) -> DeployerResult:
    return DeployerResult.model_validate(raw)

def validate_monitor(raw: dict) -> MonitorResult:
    return MonitorResult.model_validate(raw)
