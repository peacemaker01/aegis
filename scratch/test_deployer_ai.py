# scratch/test_deployer_ai.py
import asyncio
import traceback
from core.config import load_config
from core.deployer_session import run_deployer_analysis

async def main():
    cfg = load_config()
    # Solana address from user's logs
    addr = "DZNmd6Vo6vucc8ERNLDh1xnm9KjReE7witRb7d76UeXb"
    try:
        profile, result = await run_deployer_analysis(addr, cfg, stream=False, debug=True, force_refresh=True)
        print("Success! Result:", result)
    except Exception as e:
        print("CRASHED!")
        traceback.print_exc()

asyncio.run(main())
