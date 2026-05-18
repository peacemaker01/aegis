# Nansen Integration Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         AEGIS AUDIT PIPELINE V2                             │
│                      (With Nansen Intelligence)                             │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│ USER REQUEST: audit 0x... --chain eth                                         │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ LAYER 1: DATA COLLECTION                                                      │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  ┌─────────────────────┐  ┌──────────────────┐  ┌──────────────────┐       │
│  │ Etherscan           │  │ Static Analysis  │  │ Slither Scanner  │       │
│  │ • Source Code       │  │ • Mint functions │  │ • Vulnerabilities│       │
│  │ • ABI               │  │ • Honeypot       │  │ • Patterns       │       │
│  │ • Deployer          │  │ • Blacklist      │  │ • Risks          │       │
│  └─────────────────────┘  └──────────────────┘  └──────────────────┘       │
│                                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ LAYER 2: VALIDATION (GoPlus + Nansen)                                         │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  ┌──────────────────────────────┐  ┌──────────────────────────────────┐     │
│  │ GoPlus API                   │  │ [NEW] Nansen API                 │     │
│  │ • Honeypot detection         │  │ • Smart money detection          │     │
│  │ • Token security flags       │  │ • Holder composition analysis    │     │
│  │ • Ground truth source        │  │ • Deployer reputation scoring    │     │
│  │ ✓ Binary, always accurate    │  │ ✓ Contextual, institutional     │     │
│  └──────────────────────────────┘  └──────────────────────────────────┘     │
│                                                                               │
│ Nansen Data Flow:                                                             │
│  Contract Audit:                                                              │
│    └─ get_smart_money_activity() → {count, accumulating, top_holders}       │
│    └─ get_holder_composition() → {smart_money_%, institutional_%, ...}      │
│                                                                               │
│  Deployer Analysis:                                                           │
│    └─ get_wallet_label() → {label, entity_type, risk_score}                │
│    └─ get_deployer_reputation() → {success_rate, failed_contracts, ...}    │
│                                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ LAYER 3: RISK SCORING & CONTEXT                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │ parse_nansen_contract(raw_data)                                        │ │
│  │ ├─ Extract: smart_money_count, institutional_quality, signals         │ │
│  │ ├─ Identify: red flags vs green flags                                 │ │
│  │ └─ Output: Standardized Nansen schema                                 │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                               │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │ apply_nansen_to_score(base_score, nansen_data)                        │ │
│  │ ├─ Smart money > 50: -1.5 points (more legitimate)                    │ │
│  │ ├─ Institutional high: -1.0 point                                      │ │
│  │ ├─ Top 10 concentration > 80%: +1.0 point (rug risk)                  │ │
│  │ └─ Output: Adjusted risk score (0-10)                                  │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ LAYER 4: AI ANALYSIS (OpenRouter)                                            │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  Input Prompt includes:                                                       │
│  ✓ Source code                                                                │
│  ✓ Slither findings                                                           │
│  ✓ GoPlus results                                                             │
│  ✓ [NEW] Nansen Intelligence Section:                                        │
│    ├─ Smart Money Holders: 47 labeled wallets                               │
│    ├─ Institutional Quality: HIGH                                           │
│    ├─ Top 10 Concentration: 42.1%                                           │
│    ├─ Smart Money Behavior: ACCUMULATING                                    │
│    └─ Smart Money Signals: [array of insights]                             │
│                                                                               │
│  AI Model:                                                                    │
│  └─ Analyzes all context → Generates risk score & recommendation            │
│                                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ LAYER 5: CONSENSUS & FINALIZATION                                            │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ Apply Ground Truth Consistency                                       │   │
│  │ ├─ If honeypot detected (GoPlus/Static): risk_score ≥ 8.0         │   │
│  │ ├─ If Nansen detects known scammer: +3.0 points                    │   │
│  │ └─ Final clamped score: 0.0-10.0                                   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ get_nansen_recommendation(nansen_data, context)                    │   │
│  │ ├─ Smart money > 100: recommendation = "SAFE" (rare)              │   │
│  │ ├─ Top 10 conc > 95%: recommendation = "AVOID"                    │   │
│  │ ├─ Scam confidence > 80%: recommendation = "BLACKLIST"            │   │
│  │ └─ Else: keep AI recommendation                                    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ FINAL AUDIT RESULT                                                            │
├──────────────────────────────────────────────────────────────────────────────┤
│ ✓ risk_score: 6.2 (adjusted from 7.4 due to smart money presence)           │
│ ✓ recommendation: CAUTION                                                     │
│ ✓ findings: [code vulnerabilities, patterns]                                 │
│ ✓ smart_money_count: 47 (new field)                                          │
│ ✓ institutional_quality: HIGH (new field)                                    │
│ ✓ nansen_signals: [array of insights] (new field)                            │
│ ✓ positive_signals: [smart money presence, institutional backing, ...]      │
│                                                                               │
│ Rendered to:                                                                  │
│ • Rich terminal output with colors                                            │
│ • PDF report with institutional context                                       │
│ • Telegram notification with signals                                         │
└──────────────────────────────────────────────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════════════
DEPLOYER FORENSICS PIPELINE (parallel enhancement)
═══════════════════════════════════════════════════════════════════════════════

┌──────────────────────────────────────────────────────────────────────────────┐
│ USER REQUEST: deployer 0x... --chains eth bsc polygon                        │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ TRADITIONAL DEPLOYER ANALYSIS                                                 │
│ • Fetch deployment history across chains                                      │
│ • Calculate risk profile (multi-chain, unverified %, low holders %)          │
│ • Identify funder source                                                      │
│ • Build AI prompt with patterns                                               │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ [NEW] NANSEN DEPLOYER INTELLIGENCE                                            │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ get_wallet_label(deployer, chain)                                  │   │
│  │ → {label: "Uniswap: Router", entity: "market_maker", ...}         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ get_deployer_reputation(deployer)                                  │   │
│  │ ├─ total_contracts_deployed: 1,247                                │   │
│  │ ├─ success_rate: 87.3%                                            │   │
│  │ ├─ failed_contracts: 162 (rugs/honeypots)                        │   │
│  │ ├─ is_known_scammer: false                                       │   │
│  │ ├─ scam_confidence: 0.12 (low)                                   │   │
│  │ └─ platforms_used: [Uniswap, Pancakeswap, SushiSwap]            │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                               │
│  Nansen Data Added to Prompt:                                                 │
│  ├─ Entity identification (official exchange vs unknown)                     │
│  ├─ Historical reputation (track record)                                    │
│  ├─ Red/green flags from Nansen database                                    │
│  └─ Scam confidence scoring                                                 │
│                                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ AI ANALYZES COMBINED CONTEXT                                                  │
│ ├─ Pattern: deployed 50 contracts in 24 hours                                │
│ ├─ But: Nansen says "Uniswap Router" (trusted entity)                        │
│ ├─ Pattern: 5 failed contracts                                               │
│ ├─ But: Nansen shows 87.3% success rate (legitimate developer)              │
│ └─ Result: CAUTION (not AVOID) - developer uses many platforms              │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ FINAL DEPLOYER VERDICT                                                        │
│ ✓ risk_score: 4.2 (reduced from 6.5 due to Nansen reputation)               │
│ ✓ verdict: SUSPICIOUS (but not KNOWN_RUGGER)                                │
│ ✓ recommendation: CAUTION                                                     │
│ ✓ deployer_label: "Uniswap: Router" (new)                                   │
│ ✓ deployer_entity: "market_maker" (new)                                     │
│ ✓ nansen_reputation_score: 8.5/10 (new)                                     │
│ ✓ nansen_success_rate: 0.873 (new)                                          │
└──────────────────────────────────────────────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════════════
KEY DATA FLOWS
═══════════════════════════════════════════════════════════════════════════════

Contract Audit Path:
  User Request
    ↓
  core/session.py → run_audit()
    ├─ Etherscan fetch
    ├─ Static checks
    ├─ GoPlus check
    ├─ [NEW] Nansen: fetch_nansen_contract_data()
    │           ├─ get_smart_money_activity()
    │           └─ get_holder_composition()
    ├─ parse_nansen_contract() → standardize
    ├─ Apply nansen_to_score() → adjust risk
    ├─ Build prompt with Nansen context
    ├─ AI analysis
    ├─ apply ground truth
    └─ Return AuditResult (with nansen fields)

Deployer Analysis Path:
  User Request
    ↓
  core/deployer_session.py → run_deployer_analysis()
    ├─ Fetch deployments
    ├─ Calculate risk profile
    ├─ [NEW] Nansen: fetch_nansen_deployer_data()
    │           ├─ get_wallet_label()
    │           └─ get_deployer_reputation()
    ├─ parse_nansen_deployer() → standardize
    ├─ Apply nansen_to_score() → adjust risk
    ├─ Build prompt with Nansen context
    ├─ AI analysis
    ├─ Override if Nansen signals strong
    └─ Return DeployerResult (with nansen fields)


═══════════════════════════════════════════════════════════════════════════════
ERROR HANDLING & FALLBACKS
═══════════════════════════════════════════════════════════════════════════════

┌──────────────────────────────┐
│ Missing NANSEN_API_KEY       │
├──────────────────────────────┤
│ → Feature disabled           │
│ → Audit continues normally   │
│ → No Nansen fields in output │
└──────────────────────────────┘

┌──────────────────────────────┐
│ Nansen API timeout (>15s)   │
├──────────────────────────────┤
│ → Skip Nansen data           │
│ → Continue with other layers │
│ → Audit completes with base  │
│   score (no Nansen adjust)   │
└──────────────────────────────┘

┌──────────────────────────────┐
│ Rate limit exceeded          │
├──────────────────────────────┤
│ → Exponential backoff retry  │
│ → Up to 3 retries            │
│ → Then skip data             │
└──────────────────────────────┘

┌──────────────────────────────┐
│ Invalid response format      │
├──────────────────────────────┤
│ → Caught by parse functions  │
│ → Return empty dict          │
│ → Continue without error     │
└──────────────────────────────┘

═══════════════════════════════════════════════════════════════════════════════
```

## Summary

The Nansen integration adds **institutional intelligence** to Aegis's risk scoring:

1. **Smart Money Context** - Know if whales are buying/selling
2. **Deployer Reputation** - Track record from 500M+ labeled wallets
3. **Institutional Backing** - Measure fund/VC involvement  
4. **Scammer Database** - Cross-reference against known bad actors
5. **Holder Quality** - Distinguish retail from institutional holders

All integrated **seamlessly** with:
- ✅ Graceful fallback if Nansen unavailable
- ✅ Risk score adjustments (-3 to +3 points)
- ✅ AI-aware context in prompts
- ✅ Zero breaking changes to existing flows
- ✅ Full error handling
