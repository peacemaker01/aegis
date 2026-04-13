# cli/watch_cmd.py
"""
Portfolio monitor commands:
  aegis watch add    0xABC --chain bsc --label "My Token"
  aegis watch remove 0xABC --chain bsc
  aegis watch list
  aegis watch scan           (one-shot scan all)
  aegis watch run            (continuous loop)
"""
import asyncio
import time
import typer
from rich.console import Console

from core.watchlist import (
    add_entry, remove_entry, list_entries, count,
)
from core.monitor_session import run_monitor_cycle
from report.portfolio_renderer import (
    render_watchlist, render_monitor_results, render_monitor_spinner,
)
from utils.validators import is_valid_address

console = Console()
app     = typer.Typer(help="Portfolio monitor — watch contracts for risk changes.")


@app.command("add")
def watch_add(
    address:   str   = typer.Argument(..., help="Contract address to watch"),
    chain:     str   = typer.Option("eth", "--chain", "-c"),
    label:     str   = typer.Option("",    "--label", "-l", help="Friendly name"),
    threshold: float = typer.Option(6.0,   "--threshold", "-t",
                                    help="Risk score threshold to trigger alert (0-10)"),
):
    """Add a contract to your watchlist."""
    if not is_valid_address(address):
        console.print(f"[red]✗ Invalid address: {address}[/red]")
        raise typer.Exit(1)
    entry = add_entry(address, chain, label=label, alert_threshold=threshold)
    console.print(
        f"\n[green]✓ Added[/green]  {entry['label']}  "
        f"[{chain.upper()}]  threshold={threshold}\n"
        f"  Run [bold]aegis watch scan[/bold] to check it now.\n"
    )


@app.command("remove")
def watch_remove(
    address: str = typer.Argument(...),
    chain:   str = typer.Option("eth", "--chain", "-c"),
):
    """Remove a contract from your watchlist."""
    if remove_entry(address, chain):
        console.print(f"[green]✓ Removed[/green] {address[:12]}… from {chain.upper()}")
    else:
        console.print(f"[yellow]Not found in watchlist.[/yellow]")


@app.command("list")
def watch_list():
    """Show all watched contracts with their latest risk scores."""
    entries = list_entries()
    render_watchlist(entries)


@app.command("scan")
def watch_scan(
    all_results: bool = typer.Option(False, "--all", "-a",
                                     help="Show all results, not just alerts"),
):
    """One-shot scan of all watched contracts."""
    entries = list_entries()
    if not entries:
        console.print("\n[dim]Watchlist is empty. Add contracts:[/dim]")
        console.print("  [bold]aegis watch add 0xABC --chain bsc[/bold]\n")
        raise typer.Exit(0)

    from core.config import load_config, validate_keys
    config = load_config()
    if not validate_keys(config):
        raise typer.Exit(1)

    console.print(f"\n[cyan]⟳[/cyan]  Scanning {len(entries)} watched contracts…\n")

    async def _run():
        results = await run_monitor_cycle(config)
        if all_results:
            render_monitor_results(results)
        else:
            alerts = [r for r in results if r.get("alert") or r.get("error")]
            render_monitor_results(alerts if alerts else results[:1])

    asyncio.run(_run())


@app.command("run")
def watch_run(
    interval: int  = typer.Option(30,   "--interval", "-i",
                                  help="Check interval in minutes"),
    once:     bool = typer.Option(False, "--once",
                                  help="Run one cycle then exit"),
):
    """
    Continuous portfolio monitor — checks all watched contracts on a schedule.

    Example:
      aegis watch run --interval 15
    """
    entries = list_entries()
    if not entries:
        console.print("\n[dim]Watchlist empty. Add contracts first:[/dim]")
        console.print("  [bold]aegis watch add 0xABC --chain bsc[/bold]\n")
        raise typer.Exit(0)

    from core.config import load_config, validate_keys
    config = load_config()
    if not validate_keys(config):
        raise typer.Exit(1)

    render_monitor_spinner(len(entries), interval)

    async def _loop():
        while True:
            results = await run_monitor_cycle(config)
            render_monitor_results(results)
            if once:
                break
            console.print(f"  [dim]Next check in {interval} minutes…[/dim]")
            await asyncio.sleep(interval * 60)

    try:
        asyncio.run(_loop())
    except KeyboardInterrupt:
        console.print("\n[dim]Monitor stopped.[/dim]\n")
