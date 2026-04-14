# Aegis — AI Smart Contract Auditor

Terminal-based smart contract security auditor powered by AI.
Runs on Termux (Android), Linux, macOS. No API keys needed — works out of the box.

## Quick Start

```bash
chmod +x aegis
./aegis                          # first-run setup wizard

./aegis audit 0xABC123... --chain bsc
./aegis audit 0xABC123... --chain eth --model claude-sonnet
./aegis scan 0xAAA 0xBBB 0xCCC --chain polygon
```

## Config

```bash
./aegis config show
./aegis config set openrouter.model    deepseek/deepseek-r1
./aegis config models                  # list all models
./aegis config reset                   # wipe and start over
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

## Build from Source (Termux)

```bash
bash build.sh
```
