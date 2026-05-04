# analysis/mythril_integration.py
import subprocess
import json
import sys
import asyncio
from urllib.parse import urlparse
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor


def _get_mythril_cmd() -> Optional[List[str]]:
    import shutil
    mythril_bin = shutil.which('mythril')
    if mythril_bin:
        return [mythril_bin]
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'mythril', 'version'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return [sys.executable, '-m', 'mythril']
    except Exception:
        pass
    return None


def is_mythril_available() -> bool:
    return _get_mythril_cmd() is not None


MYTHRIL_SEVERITY_MAP = {
    'High': 'HIGH',
    'Medium': 'MEDIUM',
    'Low': 'LOW',
    'Informational': 'INFO',
}

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

INFURA_CHAINS = {
    'eth': 'infura-mainnet',
    'polygon': 'infura-polygon',
    'arb': 'infura-arbitrum',
    'op': 'infura-optimism',
    'base': 'infura-base',
    'avax': 'infura-avalanche',
}


def _url_to_host_port(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return url
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == 'https' else 80
    return f"{host}:{port}"


def run_mythril_on_address(
    address: str,
    chain: str,
    debug: bool = False,
    rpc_url: str = None,
    infura_id: str = None,
    timeout: int = 180
) -> List[dict]:
    mythril_cmd = _get_mythril_cmd()
    if not mythril_cmd:
        if debug:
            print("[DEBUG] Mythril not found.")
        return []

    cmd = mythril_cmd + [
        'analyze', '-a', address, '-o', 'jsonv2',
        '--execution-timeout', str(timeout)
    ]

    rpc_arg = None
    use_infura = False

    if rpc_url:
        if rpc_url.startswith("infura-"):
            use_infura = True
            rpc_arg = rpc_url
        else:
            rpc_arg = _url_to_host_port(rpc_url) if rpc_url.startswith('http') else rpc_url
    else:
        infura_network = INFURA_CHAINS.get(chain.lower())
        if infura_network and infura_id:
            use_infura = True
            rpc_arg = infura_network
        else:
            public = PUBLIC_RPC.get(chain.lower())
            if public:
                rpc_arg = _url_to_host_port(public)

    if not rpc_arg:
        if debug:
            print(f"[DEBUG] No RPC endpoint for chain '{chain}'")
        return []

    if use_infura:
        if not infura_id:
            if debug:
                print("[DEBUG] Infura ID required")
            return []
        cmd.extend(["--infura-id", infura_id])

    cmd.extend(['--rpc', rpc_arg])

    if debug:
        print(f"[DEBUG] Mythril command: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
        if debug:
            print(f"[DEBUG] Mythril return code: {result.returncode}")

        # Even if return code is non-zero, Mythril may have produced partial output.
        if not result.stdout:
            return []

        data = json.loads(result.stdout)
        return _parse_mythril_output(data, debug)

    except subprocess.TimeoutExpired:
        if debug:
            print("[DEBUG] Mythril analysis timed out")
        return []
    except json.JSONDecodeError as e:
        if debug:
            print(f"[DEBUG] JSON decode error: {e}")
        return []
    except Exception as e:
        if debug:
            print(f"[DEBUG] Mythril error: {e}")
        return []


def _parse_mythril_output(data, debug: bool = False) -> List[dict]:
    findings = []

    def find_value(obj, key):
        if isinstance(obj, dict):
            if key in obj:
                return obj[key]
            for v in obj.values():
                res = find_value(v, key)
                if res is not None:
                    return res
        elif isinstance(obj, list):
            for item in obj:
                res = find_value(item, key)
                if res is not None:
                    return res
        return None

    def extract_issues(obj):
        issues = find_value(obj, 'issues')
        if issues is None:
            return []
        if isinstance(issues, dict):
            return [issues]
        if isinstance(issues, list):
            return issues
        return []

    issues = extract_issues(data)

    for issue in issues:
        if not isinstance(issue, dict):
            continue

        if debug:
            print(f"[DEBUG] Mythril issue keys: {list(issue.keys())}")

        severity_raw = issue.get('severity', 'Low')
        severity = MYTHRIL_SEVERITY_MAP.get(severity_raw, 'LOW')

        # Prioritize swcTitle (Mythril's actual field name)
        title = (
            issue.get('swcTitle') or
            issue.get('title') or
            issue.get('name') or
            issue.get('check') or
            find_value(issue, 'swcTitle') or
            find_value(issue, 'title') or
            'Unknown Issue'
        )

        description = (
            issue.get('description') or
            find_value(issue, 'description') or
            ''
        )

        line = find_value(issue, 'line') or find_value(issue, 'sourceMap')
        swc_id = issue.get('swcID') or issue.get('swc-id') or ''

        findings.append({
            "source": "mythril",
            "title": title,
            "severity": severity,
            "description": description,
            "line": line,
            "swc_id": swc_id,
        })

        if debug:
            print(f"[DEBUG] Mythril: {severity} - {title} (line {line})")

    return findings


_mythril_executor = ThreadPoolExecutor(max_workers=1)


async def run_mythril_on_address_async(
    address: str, chain: str, debug: bool = False, timeout: float = 185.0,
    rpc_url: str = None, infura_id: str = None
) -> List[dict]:
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                _mythril_executor, run_mythril_on_address,
                address, chain, debug, rpc_url, infura_id, int(timeout)
            ),
            timeout=timeout + 5
        )
    except asyncio.TimeoutError:
        if debug:
            print(f"[DEBUG] Mythril async timed out after {timeout}s")
        return []
    except Exception as e:
        if debug:
            print(f"[DEBUG] Mythril async error: {e}")
        return []