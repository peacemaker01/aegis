# Nansen Integration - Quick Reference Guide

## 🚀 Quick Start

1. **Add API Key**:
   ```bash
   echo "NANSEN_API_KEY=your_key_here" >> .env
   ```

2. **Test Contract Audit**:
   ```bash
   ./aegis audit 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48 --chain eth
   ```

3. **Test Deployer Analysis**:
   ```bash
   ./aegis deployer 0x1111111254fb6c44bac0bed2854e76f90643097d --chains eth bsc
   ```

---

## 📁 Files Created/Modified

### Created (New Files):
| File | Lines | Purpose |
|------|-------|---------|
| [fetchers/nansen.py](fetchers/nansen.py) | 380+ | Nansen API client + endpoints |
| [analysis/nansen_check.py](analysis/nansen_check.py) | 250+ | Data parsing & risk scoring |
| [NANSEN_INTEGRATION.md](NANSEN_INTEGRATION.md) | 300+ | Complete integration guide |
| [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md) | 200+ | What was implemented |
| [NANSEN_ARCHITECTURE.md](NANSEN_ARCHITECTURE.md) | 400+ | Architecture diagrams |

### Modified (Integration Points):
| File | Changes | Type |
|------|---------|------|
| [core/config.py](core/config.py#L17) | +3 lines | Config support |
| [core/session.py](core/session.py#L24-34) | +5 lines | Import Nansen |
| [core/session.py](core/session.py#L88-105) | +25 lines | Contract audit integration |
| [core/deployer_session.py](core/deployer_session.py#L8-15) | +8 lines | Import Nansen |
| [core/deployer_session.py](core/deployer_session.py#L93-107) | +20 lines | Deployer analysis integration |
| [core/deployer_session.py](core/deployer_session.py#L126-147) | +22 lines | Nansen adjustments to result |
| [analysis/schema.py](analysis/schema.py#L37-39) | +3 fields | AuditResult Nansen fields |
| [analysis/schema.py](analysis/schema.py#L91-94) | +4 fields | DeployerResult Nansen fields |
| [ai/prompt_builder.py](ai/prompt_builder.py#L118-150) | +35 lines | Nansen in audit prompts |
| [ai/deployer_prompt.py](ai/deployer_prompt.py#L66-114) | +50 lines | Nansen in deployer prompts |

---

## 🔑 Key Functions

### Nansen API Client (fetchers/nansen.py)

```python
client = NansenClient(api_key="...")

# Get contract intelligence
smart_money = await client.get_smart_money_activity(address, chain)
holders = await client.get_holder_composition(address, chain)

# Get deployer reputation
reputation = await client.get_deployer_reputation(deployer)
label = await client.get_wallet_label(deployer, chain)
```

### Analysis Layer (analysis/nansen_check.py)

```python
# Parse Nansen data
contract_data = parse_nansen_contract(raw_nansen)
deployer_data = parse_nansen_deployer(raw_nansen)

# Adjust risk score
new_score = apply_nansen_to_score(base_score, nansen_data, "contract")

# Get recommendation override
rec = get_nansen_recommendation(nansen_data, "deployer")
```

---

## 📊 Data Fields Added

### AuditResult
```python
smart_money_count: int          # e.g., 47 labeled wallets
institutional_quality: str      # "high" | "medium" | "low"
nansen_signals: list[str]       # ["Smart Money ACCUMULATING", ...]
```

### DeployerResult
```python
deployer_label: str             # e.g., "Binance: Hot Wallet"
deployer_entity: str            # "exchange" | "fund" | ...
nansen_reputation_score: float  # 0-10
nansen_success_rate: float      # 0-1 (e.g., 0.873)
```

---

## ⚙️ Risk Score Adjustments

### Contract Scoring
| Signal | Adjustment | Rationale |
|--------|------------|-----------|
| Smart money > 50 | -1.5 | Institutional validation |
| Institutional quality high | -1.0 | Fund/VC backing |
| Top 10 concentration > 80% | +1.0 | Rug risk |
| Exchange concentration > 50% | +0.5 | Manipulation risk |

### Deployer Scoring
| Signal | Adjustment | Rationale |
|--------|------------|-----------|
| Known scammer | +3.0 | Confirmed bad actor |
| Failed contracts > 5 | +2.0 | History of failures |
| Failed contracts > 2 | +1.0 | Some failures |
| Reputation score > 8.5 | -1.5 | Strong track record |
| Success rate < 30% | +1.5 | Mostly failed |

---

## 🎯 Integration Points

### In run_audit() [core/session.py]:
```python
# Around line 88-105
if ADVANCED_ANALYSIS and not stream:
    # ... existing code ...
    
    # [NEW] Fetch Nansen data
    nansen_data = {}
    if config.get("nansen", {}).get("enabled"):
        nansen_client = NansenClient(api_key=config["nansen"]["api_key"])
        nansen_raw = await fetch_nansen_contract_data(...)
        nansen_data = parse_nansen_contract(nansen_raw)
    
    # [NEW] Apply adjustments
    if nansen_data.get("nansen_available"):
        result["smart_money_count"] = ...
        result["risk_score"] = apply_nansen_to_score(...)
```

### In run_deployer_analysis() [core/deployer_session.py]:
```python
# Around line 93-107 & 126-147
# [NEW] Fetch Nansen deployer data
if NANSEN_AVAILABLE and config.get("nansen", {}).get("enabled"):
    nansen_client = NansenClient(api_key=config["nansen"]["api_key"])
    nansen_raw = await fetch_nansen_deployer_data(...)
    nansen_data = parse_nansen_deployer(nansen_raw)

# [NEW] Apply to result
if nansen_data.get("nansen_available"):
    result["deployer_label"] = ...
    result["risk_score"] = apply_nansen_to_score(...)
```

---

## 🛡️ Error Handling

All errors are caught gracefully:

```python
try:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, headers=headers)
        if r.status_code == 200:
            return parse_response(r.json())
except Exception:
    pass

return {}  # Returns empty dict, audit continues
```

---

## 🧪 Testing

### Check if Nansen is enabled:
```python
from core.config import load_config
config = load_config()
print(config["nansen"]["enabled"])  # True if API key set
```

### Test Nansen client directly:
```python
from fetchers.nansen import NansenClient

client = NansenClient(api_key="test_key")
result = await client.get_wallet_label("0xA0b86...", "eth")
print(result)
# Expected: {"label": "USDC", "entity": "official", ...}
```

### Test in audit:
```bash
NANSEN_API_KEY=test_key ./aegis audit 0xA0b86... --chain eth --debug
# Look for: [DEBUG] Nansen smart money count: ...
```

---

## 📈 Performance

| Metric | Value |
|--------|-------|
| API calls added per audit | +1 (contract) or +1 (deployer) |
| Network latency added | 500-1500ms |
| Rate limit | 1.5 req/sec (burst 90/min) |
| Timeout threshold | 15 seconds |
| Failure mode | Graceful (continues without Nansen) |

---

## 🔗 Documentation Files

| File | Purpose | Read Time |
|------|---------|-----------|
| [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md) | What was built | 5 min |
| [NANSEN_INTEGRATION.md](NANSEN_INTEGRATION.md) | How to use it | 10 min |
| [NANSEN_ARCHITECTURE.md](NANSEN_ARCHITECTURE.md) | How it works | 15 min |
| This file | Quick reference | 3 min |

---

## 🚨 Troubleshooting

### Nansen data not appearing
**Check:**
- [ ] NANSEN_API_KEY is set in .env
- [ ] Chain is supported (eth, bsc, polygon, arb, base, op, avax, fantom, solana)
- [ ] Not rate limited (check logs for rate limit errors)

### "Nansen not available" message
**Likely causes:**
- API key not set
- API key invalid
- Network connectivity issue
- Nansen API is down

**Solution:**
- Verify key: `echo $NANSEN_API_KEY`
- Test directly: `curl -H "Authorization: Bearer $KEY" https://api.nansen.ai/...`

### Slow audit times
**Check:**
- Nansen API latency (typically <1.5s)
- Network connection quality
- Rate limiting (check terminal output)

---

## 📞 Support

For issues:
1. Check [NANSEN_INTEGRATION.md](NANSEN_INTEGRATION.md) - Troubleshooting section
2. Check debug logs: `./aegis audit ... --debug | grep Nansen`
3. Verify config: `python -c "from core.config import load_config; print(load_config()['nansen'])"`

---

## ✅ Checklist Before Going Live

- [ ] NANSEN_API_KEY added to .env
- [ ] Test audit works: `./aegis audit 0xA0b86... --chain eth`
- [ ] Test deployer works: `./aegis deployer 0x1111... --chains eth`
- [ ] Nansen fields appear in output (smart_money_count, etc.)
- [ ] Risk scores are adjusted appropriately
- [ ] No timeout errors in logs
- [ ] Rate limiting working (not hitting limits)

---

**Implementation Status**: ✅ COMPLETE & READY FOR TESTING

All 10+ files modified/created with comprehensive error handling and documentation.
