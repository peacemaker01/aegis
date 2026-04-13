#!/usr/bin/env python3
"""Aegis — The Mobile On‑Chain Security Suite"""
import typer
from rich.console import Console

from core.config import load_config, validate_keys
from core.chains import CHAINS, resolve_chain
from ai.models import get_model_id
from core.license import check_license_on_startup
import cli.config_cmd as config_mod
import cli.watch_cmd  as watch_mod

console = Console()
app = typer.Typer(
    name="aegis",
    help="[bold red]Aegis[/bold red] — The Mobile On‑Chain Security Suite",
    add_completion=False,
    rich_markup_mode="rich",
)
app.add_typer(config_mod.app, name="config")
app.add_typer(watch_mod.app,  name="watch")


# ── audit ─────────────────────────────────────────────────────────────────────
@app.command()
def audit(
    address:   str  = typer.Argument(..., help="Contract address (0x...)"),
    chain:     str  = typer.Option("eth",  "--chain", "-c",
                                   help=f"Chain: {', '.join(CHAINS.keys())}"),
    model:     str  = typer.Option(None,   "--model", "-m"),
    no_qa:     bool = typer.Option(False,  "--no-qa"),
    no_stream: bool = typer.Option(False,  "--no-stream"),
    json_out:  bool = typer.Option(False,  "--json"),
    debug:     bool = typer.Option(False,  "--debug", help="Enable debug output"),
):
    """Audit a smart contract for rug pull risks."""
    check_license_on_startup()
    from cli.audit import audit_command
    config    = load_config()
    chain_key = resolve_chain(chain)
    if chain_key not in CHAINS:
        console.print(f"[red]Unknown chain '{chain}'[/red]")
        raise typer.Exit(1)
    if not validate_keys(config): raise typer.Exit(1)
    audit_command(
        address        = address,
        chain          = chain_key,
        config         = config,
        model_override = get_model_id(model) if model else None,
        no_stream      = no_stream,
        qa_mode        = not no_qa,
        debug          = debug,
    )


# ── deployer ──────────────────────────────────────────────────────────────────
@app.command()
def deployer(
    address:   str       = typer.Argument(..., help="Deployer wallet address"),
    chain:     list[str] = typer.Option(None,  "--chain", "-c",
                           help="Chains to scan (repeat). Default: eth bsc polygon base arb"),
    model:     str  = typer.Option(None,  "--model", "-m"),
    no_stream: bool = typer.Option(False, "--no-stream"),
    json_out:  bool = typer.Option(False, "--json"),
):
    """
    Deployer forensics — scan a wallet's full contract deployment history
    across chains and detect serial rug pull patterns.
    """
    check_license_on_startup()
    from cli.deployer_cmd import deployer_command
    config = load_config()
    if not validate_keys(config): raise typer.Exit(1)
    deployer_command(
        address        = address,
        config         = config,
        chains         = list(chain) if chain else None,
        model_override = get_model_id(model) if model else None,
        no_stream      = no_stream,
        output_json    = json_out,
    )


# ── wallet ────────────────────────────────────────────────────────────────────
@app.command()
def wallet(
    address:    str   = typer.Argument(..., help="Wallet address to analyse (0x...)"),
    chain:      str   = typer.Option("eth", "--chain", "-c",
                                     help=f"Chain: {', '.join(CHAINS.keys())} (ignored if --all-chains)"),
    model:      str   = typer.Option(None,  "--model", "-m"),
    json_out:   bool  = typer.Option(False, "--json"),
    all_chains: bool  = typer.Option(False, "--all-chains", "-a",
                                     help="Scan all supported chains (overrides --chain)"),
):
    """
    Wallet tracker — fetch all token holdings and audit each contract.
    Shows portfolio-level risk score and grade.

    Examples:
      aegis wallet 0xYourAddress --chain eth
      aegis wallet 0xYourAddress --all-chains
    """
    check_license_on_startup()
    from cli.wallet_cmd import wallet_command
    config    = load_config()
    
    if all_chains:
        chains_to_scan = list(CHAINS.keys())
    else:
        chain_key = resolve_chain(chain)
        if chain_key not in CHAINS:
            console.print(f"[red]Unknown chain '{chain}'[/red]")
            raise typer.Exit(1)
        chains_to_scan = [chain_key]
    
    if not validate_keys(config): raise typer.Exit(1)
    if model:
        config["openrouter"]["model"] = get_model_id(model)
    
    wallet_command(
        address  = address,
        chains   = chains_to_scan,
        config   = config,
        json_out = json_out,
    )


# ── scan (batch) ──────────────────────────────────────────────────────────────
@app.command()
def scan(
    addresses: list[str] = typer.Argument(..., help="Multiple contract addresses"),
    chain:     str       = typer.Option("eth",  "--chain", "-c"),
    model:     str       = typer.Option(None,   "--model", "-m"),
    json_out:  bool      = typer.Option(False,  "--json"),
    debug:     bool      = typer.Option(False,  "--debug", help="Enable debug output"),
):
    """Batch audit multiple contracts at once."""
    check_license_on_startup()
    import asyncio, json as _json
    from core.session import run_audit
    from report.renderer import render_audit, render_error

    config    = load_config()
    chain_key = resolve_chain(chain)
    if not validate_keys(config): raise typer.Exit(1)
    if model: config["openrouter"]["model"] = get_model_id(model)

    async def _batch():
        for addr in addresses:
            console.rule(f"[cyan]{addr}[/cyan]")
            try:
                _, result = await run_audit(addr, chain_key, config, stream=False, debug=debug)
                if json_out:
                    console.print_json(_json.dumps(result))
                else:
                    render_audit(result, addr, chain_key)
            except Exception as e:
                render_error(str(e))
    asyncio.run(_batch())


# ── activate ──────────────────────────────────────────────────────────────────
@app.command()
def activate(
    license_key: str = typer.Argument(..., help="License key from purchase email"),
):
    """Activate Aegis with your license key."""
    from core.license import activate as _activate, get_device_id, LICENSE_FILE
    console.print(f"\n  Device ID: [cyan]{get_device_id()}[/cyan]")
    if _activate(license_key):
        console.print(
            f"[bold green]✓ License activated![/bold green]\n"
            f"  Stored: {LICENSE_FILE}\n"
        )
    else:
        console.print("[bold red]✗ Invalid license key.[/bold red]")
        raise typer.Exit(1)


# ── device-id ─────────────────────────────────────────────────────────────────
@app.command("device-id")
def device_id_cmd():
    """Show your device ID for purchasing or support."""
    from core.license import get_device_id
    console.print(f"\n  Device ID: [bold cyan]{get_device_id()}[/bold cyan]\n")


# ── version ───────────────────────────────────────────────────────────────────
@app.command()
def version():
    """Show Aegis version."""
    check_license_on_startup()
    console.print("[bold cyan]Aegis[/bold cyan] v1.0.0")
    console.print("Audit · Deployer Forensics · Wallet Tracker · Portfolio Monitor")


if __name__ == "__main__":
    app()
