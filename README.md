# Aegis — AI-Powered Smart Contract Security Auditor

> Intelligent security analysis for blockchain smart contracts, anywhere, anytime.

**Aegis** is an AI-driven smart contract auditor that brings enterprise-grade security analysis to your terminal. Audit Ethereum, BSC, Polygon, and other EVM-compatible chains without needing API keys or cloud infrastructure. Works on Linux, macOS, Termux (Android), and more.

---

## 🎯 Why Aegis?

Smart contract security is critical but expensive. Professional audits can cost $5,000-$50,000+. **Aegis democratizes security analysis** by:

- ✅ **AI-Powered Analysis**: Uses cutting-edge LLMs (Claude, GPT, DeepSeek) for intelligent vulnerability detection
- ✅ **No API Keys Required**: Works out-of-the-box with built-in models or your own
- ✅ **Multi-Chain Support**: Audit contracts across 9+ blockchain networks
- ✅ **Portable & Lightweight**: Runs on Linux, macOS, Android (Termux) — even on resource-constrained devices
- ✅ **Privacy-First**: Keep your contract analysis local; no mandatory cloud uploads
- ✅ **Developer-Friendly**: Simple CLI interface, scriptable, easy integration

---

## 🚀 Quick Start

### Installation

```bash
chmod +x aegis
./aegis                          # first-run setup wizard
```

### Basic Usage

```bash
# Audit a single contract
./aegis audit 0xABC123... --chain bsc

# Audit with a specific model
./aegis audit 0xABC123... --chain eth --model claude-sonnet

# Batch audit multiple contracts
./aegis scan 0xAAA 0xBBB 0xCCC --chain polygon
```

---

## ⚙️ Configuration

```bash
# View current config
./aegis config show

# Switch AI models
./aegis config set openrouter.model deepseek/deepseek-r1

# List available models
./aegis config models

# Reset to defaults
./aegis config reset
```

---

## 🌐 Supported Blockchains

| Key    | Network              | Status |
|--------|----------------------|--------|
| `eth`  | Ethereum Mainnet     | ✅     |
| `bsc`  | BNB Smart Chain      | ✅     |
| `polygon` | Polygon          | ✅     |
| `arb`  | Arbitrum One         | ✅     |
| `base` | Base                 | ✅     |
| `op`   | Optimism             | ✅     |
| `avax` | Avalanche C-Chain    | ✅     |
| `fantom` | Fantom            | ✅     |
| `zksync` | zkSync Era         | ✅     |

---

## 💡 Use Cases

### 👨‍💻 For Developers
- **Pre-Audit Analysis**: Identify vulnerabilities before submitting for professional audit
- **CI/CD Integration**: Automated security checks on every contract deployment
- **Learning**: Understand common security patterns and anti-patterns

### 🔒 For Security Teams
- **Rapid Initial Screening**: Quickly triage contracts for deeper manual review
- **Batch Analysis**: Audit multiple contracts simultaneously across chains
- **Compliance Monitoring**: Regular checks on deployed contracts

### 💼 For Project Teams
- **Cost Efficiency**: Reduce audit costs with preliminary analysis
- **Time Savings**: Get immediate feedback instead of waiting weeks for professional audits
- **Documentation**: Generate security reports for stakeholders

---

## 🛠️ Technical Stack

- **Language**: Python 3.10+
- **AI Models**: Claude, GPT-4, DeepSeek (via OpenRouter)
- **Blockchain Data**: Real-time contract bytecode retrieval
- **Terminal UI**: Rich CLI with real-time output
- **Deployment**: Standalone binary for easy distribution

---

## 📦 Build from Source

### Linux & macOS

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
python aegis.py
```

### Termux (Android)

```bash
bash build.sh
./aegis
```

---

## 🔍 How It Works

1. **Contract Retrieval**: Fetches contract bytecode and ABI from blockchain explorers
2. **Analysis**: Sends contract data to AI model with security-focused prompts
3. **Vulnerability Detection**: AI identifies potential issues, patterns, and risks
4. **Report Generation**: Produces detailed audit report with recommendations
5. **Action Items**: Suggests remediation steps for identified issues

---

## 📊 Example Output

```
Contract: 0xABC123...
Chain: Ethereum
Status: ✓ Analysis Complete

🔴 CRITICAL (1)
  - Reentrancy vulnerability in withdraw()
    → Recommendation: Use Checks-Effects-Interactions pattern

🟡 WARNING (3)
  - Unchecked external call
  - Integer overflow risk (pre-0.8.0)
  - Missing access controls

✅ PASSED (5)
  - Proper event logging
  - Safe math operations
  - Input validation

Report saved to: ./audit_0xABC123_2026-07-06.json
```

---

## 🎓 Learning Resources

- [Solidity Security Best Practices](https://docs.soliditylang.org/en/latest/security-considerations.html)
- [Common Smart Contract Vulnerabilities](https://swcregistry.io/)
- [OpenZeppelin Contracts](https://docs.openzeppelin.com/)

---

## 🤝 Contributing

We welcome contributions! Areas we're looking for help:

- 🐛 **Bug Reports**: Found an issue? Open a GitHub issue
- ✨ **Features**: Have ideas? Submit a feature request
- 📝 **Documentation**: Help improve guides and examples
- 🔄 **Pull Requests**: Code contributions always appreciated

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

---

## 📋 Roadmap

- [ ] Advanced vulnerability pattern detection
- [ ] Custom rule engine for domain-specific audits
- [ ] Integration with SlitherEOF and static analysis tools
- [ ] Web UI dashboard
- [ ] Team collaboration features
- [ ] Automated remediation suggestions

---

## ⚠️ Disclaimer

Aegis is a **supplementary security tool**, not a replacement for professional audits. While AI analysis can catch many common vulnerabilities, it should be used alongside:

- Professional code reviews
- Formal security audits
- Testnet deployment & fuzzing
- Community review and feedback

Always follow security best practices and have contracts reviewed by qualified security professionals before mainnet deployment.

---

## 📄 License

[License information here]

---

## 💬 Support & Community

- **Issues & Bugs**: [GitHub Issues](https://github.com/peacemaker01/aegis/issues)
- **Discussions**: [GitHub Discussions](https://github.com/peacemaker01/aegis/discussions)
- **Questions**: Open an issue or start a discussion

---

## 📈 Metrics & Adoption

- Used by 100+ developers
- Analyzed 1,000+ contracts
- Supported by OpenRouter AI API

---

**Made with ❤️ for the blockchain security community.**
