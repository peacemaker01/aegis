# DexAI — AI Smart Contract Auditor

Terminal-based smart contract security auditor powered by OpenRouter AI.
Runs on Termux (Android), Linux, macOS. One purchase = one binary.

## Quick Start

```bash
chmod +x dexai
./dexai                          # first-run setup wizard

./dexai audit 0xABC123... --chain bsc
./dexai audit 0xABC123... --chain eth --model claude-sonnet
./dexai scan 0xAAA 0xBBB 0xCCC --chain polygon
```

## Config

```bash
./dexai config show
./dexai config set openrouter.api_key  sk-or-xxxx
./dexai config set explorers.etherscan YOUREKEY
./dexai config set openrouter.model    deepseek/deepseek-r1
./dexai config models                  # list all models
./dexai config reset                   # wipe and start over
```

## Supported Chains

| Key      | Chain            |
|----------|-----------------|
| eth      | Ethereum         |
| bsc      | BNB Smart Chain  |
| polygon  | Polygon          |
| arb      | Arbitrum One     |
| base     | Base             |
| op       | Optimism         |
| avax     | Avalanche        |
| fantom   | Fantom           |
| zksync   | zkSync Era       |

## API Keys Needed

1. **OpenRouter** — openrouter.ai/keys (pay per use)
2. **Etherscan V2** — etherscan.io/apis (one key = all chains)

## Build from Source (Termux)

```bash
bash build.sh
```
