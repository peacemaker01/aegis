# services/forensics_service.py
import asyncio
import logging
import httpx
from datetime import datetime, timezone
from typing import List, Dict, Any, Callable, Awaitable

from core.config import load_config
from core.deployer_watchlist import list_watched_deployers, update_deployer
from core.session import run_scan

logger = logging.getLogger(__name__)
config = load_config()

MORALIS_API_KEY = config["moralis"]["api_key"]
HISTORY_BASE = "https://deep-index.moralis.io/api/v2.2"
POLL_INTERVAL = 60 # Check every minute

async def check_deployer_activity(deployer: Dict[str, Any], alert_callback: Callable[[Dict, Dict], Awaitable[None]]):
    """Checks for new contract deployments by a specific deployer."""
    address = deployer["address"]
    chain = deployer["chain"]
    last_tx = deployer.get("last_known_tx")
    
    headers = {"X-API-Key": MORALIS_API_KEY, "accept": "application/json"}
    params = {"chain": chain, "order": "DESC", "limit": 5}
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{HISTORY_BASE}/wallets/{address}/history",
                params=params, headers=headers, timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            txs = data.get("result", [])
            
            if not txs:
                return

            new_last_tx = txs[0].get("hash")
            if last_tx == new_last_tx:
                return # No new activity

            # Process new transactions
            for tx in txs:
                if tx.get("hash") == last_tx:
                    break
                
                # Check for contract creation: to_address is null or empty
                if not tx.get("to_address") and tx.get("receipt_status") == "1":
                    contract_address = tx.get("receipt_contract_address")
                    if contract_address:
                        logger.info(f"New deployment detected: {contract_address} by {address}")
                        
                        # Run a fast scan on the new contract
                        try:
                            contract, result = await run_scan(contract_address, chain, config, fast_mode=True)
                            await alert_callback(deployer, {"contract": contract, "result": result, "tx": tx})
                        except Exception as e:
                            logger.error(f"Error scanning new deployment {contract_address}: {e}")

            # Update last known tx
            update_deployer(address, chain, last_known_tx=new_last_tx)
            
    except Exception as e:
        logger.error(f"Error polling deployer {address} on {chain}: {e}")

async def forensics_poller(alert_callback: Callable[[Dict, Dict], Awaitable[None]]):
    """Main loop for deployer forensics polling."""
    logger.info("Starting Deployer Forensics Poller...")
    while True:
        deployers = list_watched_deployers()
        active_deployers = [d for d in deployers if d.get("active", True)]
        
        if not active_deployers:
            await asyncio.sleep(POLL_INTERVAL)
            continue

        # Check each deployer (we could do this in parallel but let's be kind to rate limits)
        for deployer in active_deployers:
            await check_deployer_activity(deployer, alert_callback)
            await asyncio.sleep(2) # Small delay between deployers
            
        await asyncio.sleep(POLL_INTERVAL)
