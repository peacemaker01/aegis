# cli/wallet_cmd.py
"""
aegis wallet 0xAddress --chain eth
Fetches all token holdings and audits each contract.
Supports multiple chains with --all-chains or multiple --chain flags.
"""
import asyncio
import json
import time
from rich.console import Console

from core.wallet_session import run_wallet_tracker
from report.portfolio_renderer import render_wallet_report, render_wallet_spinner

console = Console()


async def _run_single_chain(address: str, chain: str, config: dict, debug: bool = False) -> tuple:
    """Run wallet tracker for one chain with retry on rate limits."""
    max_retries = 3
    base_delay = 2
    for attempt in range(max_retries):
        try:
            data = await run_wallet_tracker(address, chain, config)
            return chain, data, None
        except Exception as e:
            error_msg = str(e)
            # Check for rate limit (429)
            if "429" in error_msg or "Too Many Requests" in error_msg:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    if debug:
                        console.print(f"[yellow]⚠ Rate limit on {chain.upper()}, retrying in {delay}s...[/yellow]")
                    await asyncio.sleep(delay)
                    continue
            # Non‑retryable error or final attempt failed
            return chain, None, error_msg
    return chain, None, f"Failed after {max_retries} attempts"


def wallet_command(
    address: str,
    chains: list[str],
    config: dict,
    json_out: bool = False,
    debug: bool = False,
):
    """Core wallet tracker — scans multiple chains sequentially."""
    console.print(f"\n[bold cyan]🔍 Scanning wallet {address[:10]}... across {len(chains)} chains[/bold cyan]\n")

    async def _run_all():
        results = []
        for i, chain in enumerate(chains):
            if debug:
                console.print(f"[dim]→ Processing chain {i+1}/{len(chains)}: {chain.upper()}[/dim]")
            # Render spinner for this chain
            render_wallet_spinner(address, chain)
            chain_result = await _run_single_chain(address, chain, config, debug)
            results.append(chain_result)
            # Small delay between chains to avoid overwhelming APIs
            if i < len(chains) - 1:
                await asyncio.sleep(1)

        if json_out:
            # Combine all chain results into one JSON
            combined = {}
            for chain, data, error in results:
                if error:
                    combined[chain] = {"error": error}
                elif data:
                    combined[chain] = data
            console.print_json(json.dumps(combined, default=str))
        else:
            # Render each chain separately
            for chain, data, error in results:
                if error:
                    console.print(f"\n[red]✗ Error on {chain.upper()}: {error}[/red]\n")
                elif data:
                    render_wallet_report(data)
                else:
                    console.print(f"\n[yellow]No data for {chain.upper()}[/yellow]\n")

    asyncio.run(_run_all())
