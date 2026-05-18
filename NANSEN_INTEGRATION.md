"""
NANSEN API INTEGRATION GUIDE FOR AEGIS

This document outlines the complete Nansen API integration into Aegis.

## OVERVIEW

Nansen API has been integrated into Aegis to provide:
1. Deployer reputation and wallet labeling
2. Smart money detection and institutional holder analysis  
3. Advanced risk scoring adjustments based on wallet composition
4. Multi-chain deployer forensics with reputation tracking

## FILES CREATED/MODIFIED

### NEW FILES:
1. fetchers/nansen.py
   - NansenClient: Main API client
   - fetch_nansen_deployer_data(): Get deployer reputation
   - fetch_nansen_contract_data(): Get smart money/holder composition
   - Response parsers for each endpoint

2. analysis/nansen_check.py
   - parse_nansen_deployer(): Convert to Aegis schema
   - parse_nansen_contract(): Convert to Aegis schema
   - apply_nansen_to_score(): Risk score adjustments
   - get_nansen_recommendation(): Override recommendations

### MODIFIED FILES:
1. core/config.py
   - Added NANSEN_API_KEY env variable support
   - New config section: config["nansen"]

2. core/session.py
   - Integrated Nansen contract data fetching
   - Applied Nansen adjustments to AuditResult
   - Smart money signals added to audit output

3. core/deployer_session.py
   - Integrated Nansen deployer reputation
   - Applied Nansen adjustments to DeployerResult
   - Added deployer labels and reputation scores

4. analysis/schema.py
   - Added Nansen fields to AuditResult:
     * smart_money_count: int
     * institutional_quality: str
     * nansen_signals: list[str]
   - Added Nansen fields to DeployerResult:
     * deployer_label: str
     * deployer_entity: str
     * nansen_reputation_score: float
     * nansen_success_rate: float

5. ai/prompt_builder.py
   - Updated build_audit_prompt() with Nansen intelligence section
   - Includes smart money counts, institutional quality, behavior signals

6. ai/deployer_prompt.py
   - Updated build_deployer_prompt() with Nansen intelligence
   - Includes entity label, reputation, scam confidence, red/green flags

## CONFIGURATION

Add to your .env file:
```
NANSEN_API_KEY=your_api_key_here
```

The integration will automatically:
- Check if NANSEN_API_KEY is set
- Enable/disable Nansen features based on key presence
- Fall back gracefully if Nansen API is unavailable

## USAGE FLOW

### Contract Audit Flow:
```
1. Fetch contract data (Etherscan)
2. Run static checks
3. Run GoPlus checks
4. [NEW] Fetch Nansen smart money data
5. Build AI prompt with Nansen intelligence
6. Run AI analysis
7. Apply Nansen score adjustments
8. Return final audit result
```

### Deployer Analysis Flow:
```
1. Fetch deployment history across chains
2. Calculate risk profile
3. [NEW] Fetch Nansen deployer reputation
4. Build AI prompt with Nansen intelligence
5. Run AI analysis
6. Apply Nansen score adjustments
7. Override recommendation if strong signals
8. Return final deployer result
```

## NANSEN DATA FIELDS IN RESULTS

### In AuditResult:
- **smart_money_count** (int): Number of labeled smart money wallets holding the token
- **institutional_quality** (str): "high", "medium", or "low" based on institutional holders
- **nansen_signals** (list): Array of smart money behavior signals

### In DeployerResult:
- **deployer_label** (str): e.g., "Binance: Hot Wallet", "0xDEAD...", etc.
- **deployer_entity** (str): "exchange", "fund", "market_maker", "developer", "investor", "unknown"
- **nansen_reputation_score** (float): 0-10 reputation score
- **nansen_success_rate** (float): Success rate of deployer's contracts (0-1)

## RISK SCORE ADJUSTMENTS

### For Contracts:
- Smart money count > 50: -1.5 points
- Smart money count > 20: -1.0 point
- Smart money accumulating: -0.5 points
- Institutional quality "high": -1.0 point
- Top 10 concentration > 80%: +1.0 point
- Exchange concentration > 50%: +0.5 points

### For Deployers:
- Known scammer: +3.0 points
- Failed contracts > 5: +2.0 points
- Failed contracts > 2: +1.0 point
- Reputation score > 8: -1.5 points
- Success rate < 30%: +1.5 points

## RECOMMENDATION OVERRIDES

Strong Nansen signals can override AI recommendations:

### Contract Level:
- Smart money count > 100: Recommendation = "SAFE"
- Top 10 concentration > 95%: Recommendation = "AVOID"

### Deployer Level:
- Scam confidence > 80%: Recommendation = "BLACKLIST"
- Known scammer in database: Recommendation = "AVOID"
- Reputation > 8.5 + official entity: Recommendation = "TRUST"

## API RATE LIMITING

Nansen API rate limiting: 1.5 requests/second per token bucket
- Stored in: utils/rate_limiter.py (nansen_limiter)
- Automatic backoff and retry logic

## ERROR HANDLING

The integration is designed to fail gracefully:
- Missing NANSEN_API_KEY: Features disabled, audit continues
- API timeout: Skips Nansen data, continues with other analyses
- Invalid responses: Parsed safely, returns empty dict
- Rate limit exceeded: Automatic retry with exponential backoff

## TESTING

To test Nansen integration:

```python
from fetchers.nansen import NansenClient
from analysis.nansen_check import parse_nansen_contract

client = NansenClient(api_key="your_key")
contract_addr = "0x..."
chain = "eth"

# Get smart money data
smart_money = await client.get_smart_money_activity(contract_addr, chain)
composition = await client.get_holder_composition(contract_addr, chain)

nansen_data = parse_nansen_contract({
    "smart_money": smart_money,
    "composition": composition
})

print(nansen_data)
```

## PERFORMANCE CONSIDERATIONS

1. **API Calls per Audit:**
   - Contract audit: 1 additional API call (smart money + composition in parallel)
   - Deployer analysis: 1 additional API call (deployer reputation + label)

2. **Latency:**
   - Nansen calls: ~500-1500ms per request
   - Added to overall audit time: +0.5-1.5 seconds

3. **Caching:**
   - Nansen results are cached alongside audit results
   - Cache key: cache:{chain}:{address}

## MONITORING & DEBUGGING

Enable debug output:
```python
# In session.py / deployer_session.py
result = await run_audit(address, chain, config, debug=True)

# Output includes:
# [DEBUG] Nansen deployer label: ...
# [DEBUG] Nansen deployer reputation: ...
# [DEBUG] Nansen smart money count: ...
# [DEBUG] Nansen holder composition: ...
```

## FUTURE ENHANCEMENTS

Potential improvements for future versions:
1. Batch Nansen queries for multiple contracts
2. Historical Nansen data trends
3. Nansen X/whale notification integration
4. Custom scoring weights for Nansen signals
5. Nansen portfolio tracking integration
6. Real-time smart money alert system

## SUPPORT & TROUBLESHOOTING

### "Nansen data not appearing in results"
- Check NANSEN_API_KEY is set in .env
- Verify chain support (eth, bsc, polygon, arb, base, op, avax, fantom, solana)
- Check rate limiting hasn't blocked requests

### "Empty smart_money response"
- Contract may have no smart money holders
- Try contract with known large player (e.g., popular tokens)

### "Timeout errors from Nansen"
- Increase timeout in fetchers/nansen.py: timeout=15 (default)
- Check network connectivity
- Verify API key is valid

## INTEGRATION CHECKLIST

- [x] Nansen API client created
- [x] Config support added
- [x] Contract audit integration
- [x] Deployer analysis integration
- [x] Schema updates for Nansen fields
- [x] Risk score adjustment logic
- [x] Prompt builder updates
- [x] Error handling
- [x] Rate limiting
- [x] Documentation
- [ ] Unit tests (recommended for production)
- [ ] End-to-end testing
"""
