#!/usr/bin/env python3
"""Standalone test for Solana Deployer Forensics module with verbose debug output."""
import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import load_config
from core.deployer_session import run_deployer_analysis

# A known Solana address (e.g. the configured treasury wallet or token mint)
TEST_DEPLOYER = "Gbnyp3og4hZiF67wdTiqAE7ieiNgM4FjLWAM9UmoRRzS"

async def main():
    print(f"\n🔍 Solana Deployer Forensics: {TEST_DEPLOYER}\n")

    config = load_config()
    try:
        profile, result = await run_deployer_analysis(
            TEST_DEPLOYER, config,
            stream=False, debug=True, force_refresh=True   # <-- bypass cache
        )
        print("\n" + "="*50)
        print("FINAL SOLANA REPORT")
        print("="*50)
        print(f"Total Deployments: {profile.get('total_deployments')}")
        print(f"Funder: {profile.get('funder')}")
        print(f"Reputation Score: {result.get('reputation_score', 'N/A')}/100")
        print(f"Verdict: {result.get('verdict')}")
        print(f"Recommendation: {result.get('recommendation')}")
        print(f"\nSummary: {result.get('summary')}\n")
        if result.get('red_flags'):
            print("Red Flags:")
            for f in result['red_flags']:
                print(f"  • {f}")
        print("\nFindings:")
        for f in result.get('findings', [])[:5]:
            print(f"  [{f.get('severity')}] {f.get('title')}: {f.get('description')}")
    except Exception as e:
        print(f"❌ Analysis failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
