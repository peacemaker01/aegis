# Mythril Deep Analysis - Infura RPC

Mythril performs **symbolic execution** on deployed bytecode - optional deep analysis that enhances findings.

## How it Works

**Supported via Infura** (built-in, no API key needed):
- ✅ Ethereum Mainnet (`infura-mainnet`)
- ✅ Polygon (`infura-polygon`)
- ✅ Arbitrum (`infura-arbitrum`)
- ✅ Optimism (`infura-optimism`)
- ✅ Avalanche (`infura-avalanche`)

**Not supported by Mythril** (fallback to HTTP RPC):
- ⚠️ BSC, Base, Celo, Fantom, zkSync, Gnosis (may timeout)

## Setup

Mythril works **automatically** for Ethereum, Polygon, Arbitrum, Optimism, and Avalanche. No configuration needed.

### Optional: Store Infura Key

If you have an Infura account and want to store the API key:

1. Get API key from https://infura.io/
2. Edit `~/.aegis/config.json`:

```json
{
  "explorers": {
    "etherscan": "",
    "infura": "abc123def456..."
  }
}
```

**Note:** Mythril's CLI doesn't use this key for public networks, but it may be useful for future enhancements.

### Run Audit with Deep Analysis

```bash
python main.py audit 0x1234... -c eth --deep
```

**Expected output:**
```
[DEBUG] 🔄 Started Mythril background analysis (up to 45s)
[DEBUG] Using Infura network: infura-mainnet
```

## Supported Networks

| Network | Mythril Format | Works |
|---------|----------------|-------|
| Ethereum | `infura-mainnet` | ✅ Yes |
| Polygon | `infura-polygon` | ✅ Yes |
| Arbitrum | `infura-arbitrum` | ✅ Yes |
| Optimism | `infura-optimism` | ✅ Yes |
| Avalanche | `infura-avalanche` | ✅ Yes |
| BSC, Base, others | `HOST:PORT` | ⚠️ Unreliable |

## Execution Model

1. **Slither runs** → Fast, finds ~20-50 issues (~2-3s)
2. **Mythril starts in background** → Bytecode analysis (~30-45s)
3. **AI synthesis** → Combines both results
4. **Report generated** → With merged findings

**Result:** Audit completes in ~10-15s with Slither + AI, Mythril findings added if ready.

## Timeout Behavior

- **45s limit** on Mythril background task
- If timeout: audit continues successfully with Slither findings
- **No errors** - Mythril is optional enhancement

## Troubleshooting

**Problem:** "Invalid RPC argument, use 'ganache', 'infura-[network]', or 'HOST:PORT'"

**Cause:** The config is passing wrong format

**Solution:** Mythril expects one of:
- `infura-mainnet`, `infura-polygon`, etc. (built-in Infura support)
- `ganache` (for local Ganache)
- `localhost:8545` (custom RPC in HOST:PORT format)

**Problem:** Mythril keeps timing out

**Solution:** This is expected for complex contracts. The audit still completes. Mythril is optional.

**Problem:** Want to use different RPC?

**Solution:** Currently hardcoded to public Infura. For custom:
1. Edit [analysis/mythril_integration.py](analysis/mythril_integration.py#L150-L160)
2. Change `fallback_rpc` dict with your RPC endpoint
3. Or run local Ganache and use `--rpc ganache`

## Performance Tips

- **First audit slower**: Mythril may hang ~15s while connecting
- **Subsequent audits faster**: Background execution is efficient
- **Use `--debug`** to see Mythril progress:

```bash
python main.py audit 0x... -c eth --debug --deep 2>&1 | grep -i mythril
```

- **Skip Mythril** if you just want Slither: don't use `--deep`

## Note

Mythril analysis quality depends on:
- Contract complexity (more complex = longer analysis)
- RPC endpoint reliability
- Network congestion

The report is **always useful** with or without Mythril findings.


