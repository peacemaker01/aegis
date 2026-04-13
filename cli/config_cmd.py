# cli/config_cmd.py
import json

import typer
from rich.console import Console
from rich.table import Table
from rich import box

from core.config import load_config, save_config, set_value, DEFAULT_CONFIG
from ai.models import list_models

console = Console()
app     = typer.Typer(help="Manage Aegis settings.")


@app.command("show")
def show_config():
    """Print current config (keys are masked)."""
    config = load_config()
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    t.add_column("Key",   style="bold cyan", width=30)
    t.add_column("Value", width=50)

    def _add(key, val):
        if "key" in key.lower() and val:
            val = val[:8] + "..." + val[-4:]
        t.add_row(key, str(val))

    for section, data in config.items():
        if isinstance(data, dict):
            for k, v in data.items():
                _add(f"{section}.{k}", v)
        else:
            _add(section, data)
    console.print(t)


@app.command("set")
def set_cmd(
    key:   str = typer.Argument(..., help="e.g. openrouter.api_key"),
    value: str = typer.Argument(..., help="New value"),
):
    """Set a config value.  aegis config set openrouter.model deepseek/deepseek-r1"""
    set_value(key, value)


@app.command("models")
def list_models_cmd():
    """Show all available OpenRouter models."""
    t = Table(title="Available Models", box=box.SIMPLE_HEAVY)
    t.add_column("Key",   style="cyan")
    t.add_column("Label", style="bold")
    t.add_column("Cost (in/out per 1M tok)")
    t.add_column("Note", style="dim")

    for m in list_models():
        cost = "FREE" if m["cost_in"] == 0 else f"${m['cost_in']:.2f} / ${m['cost_out']:.2f}"
        t.add_row(m["key"], m["label"], cost, m["note"])
    console.print(t)


@app.command("reset")
def reset_config(
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Reset all config to defaults."""
    if not confirm:
        typer.confirm("This will erase your config. Continue?", abort=True)
    save_config(DEFAULT_CONFIG)
    console.print("[green]✓ Config reset to defaults.[/green]")
