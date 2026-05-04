#!/usr/bin/env python3
"""Test EVM scan in fast and deep modes."""
import asyncio, os, sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import load_config
from core.session import run_scan

TEST_ADDRESS = "0xd28E716fa02543962b3129f9E4D919C65F434444"
TEST_CHAIN = "bsc"

async def main(deep: bool = False):
    mode = "DEEP" if deep else "FAST"
    print(f"\n🔍 {mode} SCAN: {TEST_ADDRESS} on {TEST_CHAIN.upper()}\n")
    config = load_config()
    contract, result = await run_scan(
        address=TEST_ADDRESS,
        chain_name=TEST_CHAIN,
        config=config,
        stream=False,
        debug=True,
        fast_mode=not deep,
    )

    score = result.get('risk_score')
    rec = result.get('recommendation')
    print(f"Risk Score: {score}/10" if score else "Risk Score: N/A")
    print(f"Verdict: {rec}")
    print(f"Summary: {result.get('summary')}")

    # Count findings
    raw = result.get('_raw', {})
    slither = raw.get('slither', [])
    actual = [f for f in slither if not f.get("_slither_metadata") and not f.get("_slither_human_summary")]
    print(f"Slither findings: {len(actual)}")
    if deep:
        print("(Deep mode includes LOW severity upgradeability detectors)")

    static = raw.get('static', {}).get('static_findings', [])
    print(f"Static findings: {len(static)}")
    goplus_avail = raw.get('goplus', {}).get('goplus_available', False)
    print(f"GoPlus: {'Available' if goplus_avail else 'Unavailable'}")

if __name__ == "__main__":
    import sys
    deep = "--deep" in sys.argv
    asyncio.run(main(deep=deep))