#!/data/data/com.termux/files/usr/bin/bash
# Wrapper to masquerade as solc for Slither
REAL_SOLC="/data/data/com.termux/files/home/dexai_acc/solidity/build/solc/solc"

if [ "$1" = "--version" ]; then
    # Fake a stable version that Slither accepts
    echo "solc, the solidity compiler commandline interface"
    echo "Version: 0.8.24+commit.e11b9ed9.Linux.clang"
else
    exec "$REAL_SOLC" "$@"
fi
