#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Aegis Build Script
# Compiles to a single self-contained binary for Termux (aarch64-linux)
#
# Usage:
#   bash build.sh          → full build
#   bash build.sh --clean  → wipe dist/ then build
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

BINARY_NAME="aegis"
VERSION="1.0.0"

# ── Colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${CYAN}→${NC}  $*"; }
ok()    { echo -e "${GREEN}✓${NC}  $*"; }
warn()  { echo -e "${YELLOW}⚠${NC}  $*"; }
error() { echo -e "${RED}✗${NC}  $*"; exit 1; }

echo -e "\n${BOLD}Aegis Build System v${VERSION}${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Clean ──────────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--clean" ]]; then
    info "Cleaning previous build..."
    rm -rf dist/ build/ *.spec __pycache__
    rm -rf ~/.cache/pyinstaller   # Clear PyInstaller cache
    ok "Cleaned"
fi

# ── Pre-flight checks ─────────────────────────────────────────────────────────
info "Checking Python version..."
python3 --version || error "Python 3 not found. Install: pkg install python (Termux)"

info "Checking architecture..."
ARCH=$(uname -m)
echo "  Architecture: ${ARCH}"
if [[ "$ARCH" != "aarch64" && "$ARCH" != "arm64" ]]; then
    warn "Not running on aarch64 — binary may not work in Termux"
fi

# ── Check public key is set ───────────────────────────────────────────────────
info "Checking license public key..."
if grep -q "REPLACETHIS" core/license.py; then
    error "Public key not set in core/license.py\n  Run: python keygen.py generate\n  Then paste the public key into core/license.py → BAKED_PUBLIC_KEY_PEM"
fi
ok "Public key found"

# ── Install dependencies ──────────────────────────────────────────────────────
info "Installing dependencies..."
pip install \
    httpx==0.27.0 \
    typer==0.12.3 \
    rich==13.7.1 \
    cryptography==42.0.5 \
    pyinstaller \
    slither-analyzer \
    crytic-compile \
    --break-system-packages \
    --quiet
ok "Dependencies installed"

# ── Prepare solc binary (copy to a plain file to avoid directory issues) ──────
SOLC_SOURCE="./solidity/build/solc/solc"
SOLC_COPY="./solc"
SOLC_WRAPPER="./solc-wrapper.sh"

if [[ ! -f "$SOLC_SOURCE" ]]; then
    error "solc binary not found at $SOLC_SOURCE"
fi
cp "$SOLC_SOURCE" "$SOLC_COPY"
chmod +x "$SOLC_COPY"
ok "solc copied to $SOLC_COPY"

if [[ ! -f "$SOLC_WRAPPER" ]]; then
    error "solc-wrapper.sh not found at $SOLC_WRAPPER"
fi
chmod +x "$SOLC_WRAPPER"
ok "solc-wrapper found"

# ── Build with PyInstaller ────────────────────────────────────────────────────
info "Running PyInstaller..."

pyinstaller \
    --onefile \
    --name "${BINARY_NAME}" \
    --strip \
    --noupx \
    --log-level WARN \
    --add-data "${SOLC_COPY}:." \
    --add-data "${SOLC_WRAPPER}:." \
    --hidden-import typer \
    --hidden-import rich \
    --hidden-import httpx \
    --hidden-import cryptography \
    --hidden-import cryptography.hazmat.primitives.asymmetric.ed25519 \
    --hidden-import slither \
    --hidden-import slither.detectors \
    --hidden-import crytic_compile \
    --hidden-import web3 \
    main.py

# ── Clean up temporary copy ───────────────────────────────────────────────────
rm -f "$SOLC_COPY"
ok "Cleaned up temporary solc copy"

# ── Verify binary ─────────────────────────────────────────────────────────────
BINARY="dist/${BINARY_NAME}"
if [[ ! -f "$BINARY" ]]; then
    error "Build failed — binary not found at ${BINARY}"
fi

SIZE=$(du -sh "$BINARY" | cut -f1)
ok "Binary built: ${BINARY}  (${SIZE})"

# ── Smoke test ────────────────────────────────────────────────────────────────
info "Running smoke test..."
AEGIS_DEV=1 "./${BINARY}" version
ok "Smoke test passed"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Build Complete${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  Binary:  ${GREEN}${BINARY}${NC}"
echo -e "  Size:    ${SIZE}"
echo -e "  Arch:    ${ARCH}"
echo ""
echo "  Deploy:"
echo "    chmod +x dist/aegis"
echo "    cp dist/aegis ~/bin/aegis        # Termux"
echo "    cp dist/aegis /usr/local/bin/    # Linux"
echo ""
echo "  Test:"
echo "    AEGIS_DEV=1 aegis --help"
echo "    AEGIS_DEV=1 aegis audit 0xBB9bc244D798123fDe783fCc1C72d3Bb8C189413 --chain eth"
echo ""
