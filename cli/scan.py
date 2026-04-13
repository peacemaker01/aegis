"""
cli/scan.py
Batch scan multiple contracts at once.
"""
import asyncio
import time
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from fetchers.etherscan import fetch_all, CHAINS
from ai.client import OpenRouterClient, OpenRouterError
from ai.prompt_builder import build_audit_prompt
from report.renderer import render_scan_summary, render_error, render_warning
from utils.cache import get_cached, set_cached
from utils.validators import is_valid_address
from utils.exporter import export_batch_report
from core.config import validate_config

console = Console()


async def scan_command(
    addresses: list[str],
    chain:     str,
    save:      bool,
    config:    dict,
):
    """Batch audit multiple contracts."""

    issues = validate_config(config)
    for issue in issues:
        if issue.startswith("❌"):
            render_error(issue)
            return
        render_warning(issue)

    if chain not in CHAINS:
        render_error(f"Unknown chain '{chain}'.")
        return

    # Clean and validate addresses
    clean_addrs = []
    for addr in addresses:
        addr = addr.strip()
        if is_valid_address(addr):
            clean_addrs.append(addr)
        else:
            render_warning(f"Skipping invalid address: {addr}")

    if not clean_addrs:
        render_error("No valid addresses provided.")
        return

    console.print(
        f"\n[bold cyan]🔍 Batch Scan[/bold cyan] — "
        f"{len(clean_addrs)} contracts on [cyan]{CHAINS[chain]['name']}[/cyan]\n"
    )

    or_client = OpenRouterClient(
        api_key     = config["openrouter"]["api_key"],
        model       = config["openrouter"]["model"],
        max_tokens  = config["openrouter"]["max_tokens"],
        temperature = config["openrouter"]["temperature"],
    )

    results = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Auditing contracts...", total=len(clean_addrs))

        for addr in clean_addrs:
            progress.update(task, description=f"[cyan]{addr[:16]}...[/cyan]")

            try:
                # Fetch
                contract = get_cached(addr, chain)
                if not contract:
                    contract = await fetch_all(
                        address=addr,
                        chain=chain,
                        api_key=config["explorers"].get("etherscan", ""),
                    )
                    set_cached(addr, chain, contract)

                # Audit (non-streaming for batch)
                messages = build_audit_prompt(contract)
                audit    = await or_client.complete(messages)

                results.append({
                    "address":  addr,
                    "verified": contract.get("verified", False),
                    "audit":    audit,
                    "contract": contract,
                })

            except OpenRouterError as e:
                results.append({
                    "address": addr,
                    "error":   str(e),
                    "audit":   {"risk_score": 0, "recommendation": "ERROR"},
                })
            except Exception as e:
                results.append({
                    "address": addr,
                    "error":   str(e),
                    "audit":   {"risk_score": 0, "recommendation": "ERROR"},
                })

            progress.advance(task)
            # Small delay between audits to respect rate limits
            await asyncio.sleep(1)

    # Render summary table
    render_scan_summary(results)

    if save:
        path = export_batch_report(results)
        console.print(f"\n[dim]📄 Batch report saved: {path}[/dim]\n")
