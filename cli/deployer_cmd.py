# cli/deployer_cmd.py
"""
CLI command: aegis deployer <wallet_address>

Runs full deployer forensics — finds every contract they've deployed
across chains, enriches with token data, and runs AI risk analysis.
"""
import asyncio
import json

import typer
from rich.console import Console

from core.deployer_session import run_deployer_analysis
from core.chains import CHAINS
from report.deployer_renderer import (
    render_deployer_report,
    render_deployer_spinner,
)

console = Console()

# Default chains to scan (most active rug chains)
DEFAULT_CHAINS = ["eth", "bsc", "polygon", "base", "arb"]


def deployer_command(
    address: str,
    config: dict,
    chains: list[str] | None = None,
    model_override: str | None = None,
    no_stream: bool = False,
    output_json: bool = False,
):
    """Core deployer forensics flow."""
    if model_override:
        config["openrouter"]["model"] = model_override

    chains_to_scan = chains or DEFAULT_CHAINS

    render_deployer_spinner(address, chains_to_scan)

    async def _run():
        try:
            profile, result = await run_deployer_analysis(
                address, config,
                chains=chains_to_scan,
                stream=not no_stream,
            )
        except ValueError as e:
            console.print(f"\n[red]✗ {e}[/red]\n")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"\n[red]✗ Error: {e}[/red]\n")
            raise typer.Exit(1)

        if no_stream:
            report = result
        else:
            raw_json = ""
            async for token in result:
                raw_json += token
            console.print()
            try:
                report = json.loads(raw_json)
            except json.JSONDecodeError:
                console.print("[red]✗ AI returned malformed JSON — try again[/red]")
                raise typer.Exit(1)

        if output_json:
            console.print_json(json.dumps({
                "profile": profile,
                "analysis": report,
            }))
        else:
            render_deployer_report(report, profile)

        # Show the contract addresses for quick follow-up auditing
        deployments = profile.get("deployments", [])
        if deployments and not output_json:
            console.print(
                "\n[dim]Tip: Audit individual contracts with:[/dim]"
            )
            for d in deployments[:3]:
                console.print(
                    f"  [cyan]aegis audit {d['contract_address']} "
                    f"--chain {d['chain']}[/cyan]"
                )
            console.print()

    asyncio.run(_run())
