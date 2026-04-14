# analysis/mythril_integration.py
"""
Mythril integration for Aegis – symbolic execution engine.

Uses contract address analysis (bytecode) rather than source code.
Supports user‑provided RPC URLs (from config) and falls back to public endpoints.
Converts full HTTP URLs to HOST:PORT format required by Mythril.
"""

import subprocess
import json
import sys
import asyncio
from urllib.parse import urlparse
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor

# ──────────────────────────────────────────────────────────────
# Mythril detection
# ──────────────────────────────────────────────────────────────

def _get_mythril_cmd() -> Optional[List[str]]:
    """Return command to run mythril as list (binary or python -m)."""
    import shutil
    mythril_bin = shutil.which('mythril')
    if mythril_bin:
        return [mythril_bin]
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'mythril', 'version'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return [sys.executable, '-m', 'mythril']
    except Exception:
        pass
    return None


def is_mythril_available() -> bool:
    return _get_mythril_cmd() is not None


# ──────────────────────────────────────────────────────────────
# Severity mapping
# ──────────────────────────────────────────────────────────────

MYTHRIL_SEVERITY_MAP = {
    'High': 'HIGH',
    'Medium': 'MEDIUM',
    'Low': 'LOW',
    'Informational': 'INFO',
}


# ──────────────────────────────────────────────────────────────
# Public fallback RPC endpoints (full URLs, will be converted to HOST:PORT)
# ──────────────────────────────────────────────────────────────

PUBLIC_RPC = {
    'eth': 'https://cloudflare-eth.com',
    'bsc': 'https://bsc-dataseed.binance.org/',
    'polygon': 'https://polygon-rpc.com/',
    'arb': 'https://arb1.arbitrum.io/rpc',
    'base': 'https://mainnet.base.org/',
    'op': 'https://mainnet.optimism.io',
    'avax': 'https://api.avax.network/ext/bc/C/rpc',
    'fantom': 'https://rpc.fantom.network/',
    'zksync': 'https://mainnet.era.zksync.io',
    'gnosis': 'https://rpc.gnosischain.com/',
}


# ──────────────────────────────────────────────────────────────
# Helper to convert URL to HOST:PORT
# ──────────────────────────────────────────────────────────────

def _url_to_host_port(url: str) -> str:
    """Convert a URL to HOST:PORT format for Mythril's --rpc argument."""
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return url  # fallback
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == 'https' else 80
    return f"{host}:{port}"


# ──────────────────────────────────────────────────────────────
# Core analysis function
# ──────────────────────────────────────────────────────────────

def run_mythril_on_address(
    address: str,
    chain: str,
    debug: bool = False,
    rpc_url: str = None
) -> List[dict]:
    """
    Run Mythril on a deployed contract address.

    Args:
        address: Contract address (0x...)
        chain: Chain key (eth, bsc, polygon, etc.)
        debug: Print debug output
        rpc_url: Optional custom RPC URL (overrides default)

    Returns:
        List of findings (empty on error or if Mythril not installed)
    """
    mythril_cmd = _get_mythril_cmd()
    if not mythril_cmd:
        if debug:
            print("[DEBUG] Mythril not found. Install with: pip install mythril")
        return []

    # Determine RPC argument
    if rpc_url:
        rpc_arg = rpc_url
    else:
        rpc_arg = PUBLIC_RPC.get(chain)
        if not rpc_arg:
            if debug:
                print(f"[DEBUG] No RPC endpoint for chain '{chain}', skipping Mythril")
            return []

    # Convert full URL to HOST:PORT if it looks like a URL
    if rpc_arg.startswith('http'):
        rpc_arg = _url_to_host_port(rpc_arg)

    # Build command
    cmd = mythril_cmd + [
        'analyze',
        '-a', address,
        '--rpc', rpc_arg,
        '-o', 'jsonv2',
        '--execution-timeout', '30',
    ]

    if debug:
        print(f"[DEBUG] ✓ Mythril found: {' '.join(mythril_cmd)}")
        print(f"[DEBUG] Analyzing address: {address}")
        print(f"[DEBUG] RPC: {rpc_arg}")
        print(f"[DEBUG] Command: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if debug:
            print(f"[DEBUG] Mythril return code: {result.returncode}")

        if not result.stdout:
            if debug:
                print("[DEBUG] Mythril produced no output")
            return []

        # Parse JSON (Mythril outputs a JSON array of result objects)
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            if debug:
                print(f"[DEBUG] JSON decode error: {e}")
                print(f"[DEBUG] Raw output: {result.stdout[:300]}")
            return []

        return _parse_mythril_output(data, debug)

    except subprocess.TimeoutExpired:
        if debug:
            print("[DEBUG] Mythril analysis timed out after 60 seconds")
        return []
    except Exception as e:
        if debug:
            print(f"[DEBUG] Mythril error: {e}")
        return []


def _parse_mythril_output(data, debug: bool = False) -> List[dict]:
    """Parse Mythril JSON output (jsonv2 format) into Aegis findings."""
    findings = []

    # Mythril returns a list of analysis result objects, each with an 'issues' key
    if isinstance(data, list):
        all_issues = []
        for result_obj in data:
            if isinstance(result_obj, dict):
                issues = result_obj.get('issues', [])
                if issues:
                    all_issues.extend(issues)
        issues = all_issues
    elif isinstance(data, dict):
        issues = data.get('issues', [])
    else:
        if debug:
            print(f"[DEBUG] Unexpected Mythril output type: {type(data)}")
        return []

    for issue in issues:
        if not isinstance(issue, dict):
            continue

        severity_raw = issue.get('severity', 'Low')
        severity = MYTHRIL_SEVERITY_MAP.get(severity_raw, 'LOW')

        title = (
            issue.get('title') or
            issue.get('name') or
            issue.get('check') or
            issue.get('swc-title') or
            'Unknown Issue'
        )

        description = issue.get('description', '')

        # Extract location
        locations = issue.get('locations', [])
        line = None
        filename = ''
        if locations:
            loc = locations[0] if isinstance(locations, list) else locations
            if isinstance(loc, dict):
                filename = loc.get('filename', '')
                line = loc.get('line', None)

        swc_id = issue.get('swc-id', '')
        swc_title = issue.get('swc-title', '')

        finding = {
            "source": "mythril",
            "detector": title,
            "severity": severity,
            "confidence": "MEDIUM",
            "title": title,
            "description": description,
            "line": line,
            "filename": filename,
            "swc_id": swc_id,
            "swc_title": swc_title,
        }
        findings.append(finding)

        if debug:
            print(f"[DEBUG] Mythril: {severity} - {title} (line {line})")

    return findings


# ──────────────────────────────────────────────────────────────
# Async background execution (for non‑blocking deep analysis)
# ──────────────────────────────────────────────────────────────

_mythril_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mythril-bg")


async def run_mythril_on_address_async(
    address: str,
    chain: str,
    debug: bool = False,
    timeout: float = 45.0,
    rpc_url: str = None
) -> List[dict]:
    """
    Run Mythril asynchronously in background thread with timeout.
    """
    loop = asyncio.get_event_loop()
    try:
        findings = await asyncio.wait_for(
            loop.run_in_executor(
                _mythril_executor,
                run_mythril_on_address,
                address,
                chain,
                debug,
                rpc_url
            ),
            timeout=timeout
        )
        return findings
    except asyncio.TimeoutError:
        if debug:
            print(f"[DEBUG] Mythril background task timed out after {timeout}s (audit continued without it)")
        return []
    except Exception as exc:
        if debug:
            print(f"[DEBUG] Mythril background error: {exc}")
        return []