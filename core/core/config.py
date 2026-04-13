# core/config.py
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt

from ai.models import MODELS, DEFAULT_MODEL_KEY

CONFIG_DIR   = Path.home() / ".aegis"
CONFIG_FILE  = CONFIG_DIR / "config.json"
LICENSE_FILE = CONFIG_DIR / "license.key"

console = Console()

DEFAULT_CONFIG: dict = {
    "openrouter": {
        "api_key":     "",
        "model":       MODELS[DEFAULT_MODEL_KEY]["id"],
        "model_key":   DEFAULT_MODEL_KEY,
        "temperature": 0.1,
        "max_tokens":  4000,
    },
    "explorers": {
        "etherscan": "",
    },
    "preferences": {
        "default_chain":  "eth",
        "stream_output":  True,
        "save_reports":   False,
        "cache_ttl_secs": 3600,
    },
}


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return run_setup_wizard()
    try:
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        return _deep_merge(DEFAULT_CONFIG, data)
    except (json.JSONDecodeError, OSError) as e:
        console.print(f"[red]Config error: {e}[/red]")
        sys.exit(1)


def save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def set_value(key: str, value: str) -> None:
    config = load_config()
    keys   = key.split(".")
    target = config
    for k in keys[:-1]:
        if k not in target:
            console.print(f"[red]Unknown key: {key}[/red]")
            return
        target = target[k]
    final_key = keys[-1]
    if final_key not in target:
        console.print(f"[red]Unknown key: {key}[/red]")
        return
    existing = target[final_key]
    if isinstance(existing, bool):
        value = value.lower() in ("true", "1", "yes")
    elif isinstance(existing, int):
        value = int(value)
    target[final_key] = value
    save_config(config)
    console.print(f"[green]✓ Set {key} = {value}[/green]")


def validate_keys(config: dict) -> bool:
    ok = True
    if not config["openrouter"]["api_key"]:
        console.print(
            "[red]✗ OpenRouter API key missing.[/red]\n"
            "  Run: [bold]aegis config set openrouter.api_key YOUR_KEY[/bold]"
        )
        ok = False
    if not config["explorers"]["etherscan"]:
        console.print(
            "[yellow]⚠ No Etherscan key — limited data available.[/yellow]"
        )
    return ok


def run_setup_wizard() -> dict:
    config = _deep_merge(DEFAULT_CONFIG, {})

    console.print("\n[bold cyan]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold cyan]")
    console.print("[bold cyan]  Aegis — First Time Setup[/bold cyan]")
    console.print("[bold cyan]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold cyan]\n")

    console.print("[bold]Step 1/3 — OpenRouter API Key[/bold]")
    console.print("  Get one free at openrouter.ai\n")
    or_key = Prompt.ask("  Enter your OpenRouter API key", password=True)
    config["openrouter"]["api_key"] = or_key.strip()

    console.print("\n[bold]Step 2/3 — Default AI Model[/bold]\n")
    for i, (k, m) in enumerate(MODELS.items(), 1):
        cost = "FREE" if m["cost_in"] == 0 else f"${m['cost_in']:.2f}/${m['cost_out']:.2f}/1M tok"
        console.print(f"  [cyan]{i}[/cyan]. [bold]{m['label']}[/bold]  [dim]{cost}[/dim]")
        console.print(f"     {m['note']}")
    console.print()

    model_keys = list(MODELS.keys())
    choice = Prompt.ask("  Choose model [1-4]", choices=["1","2","3","4"], default="2")
    chosen_key = model_keys[int(choice) - 1]
    config["openrouter"]["model"]     = MODELS[chosen_key]["id"]
    config["openrouter"]["model_key"] = chosen_key
    console.print(f"\n  [green]✓ {MODELS[chosen_key]['label']} selected[/green]")

    console.print("\n[bold]Step 3/3 — Etherscan V2 API Key[/bold]")
    console.print("  One key = ETH + BSC + Polygon + Base + Arb + 50 more chains")
    console.print("  Get one free at etherscan.io/apis\n")
    eth_key = Prompt.ask("  Enter Etherscan key (Enter to skip)", default="")
    if eth_key.strip():
        config["explorers"]["etherscan"] = eth_key.strip()
        console.print("  [green]✓ Saved[/green]")
    else:
        console.print("  [yellow]⚠ Skipped[/yellow]")

    save_config(config)
    console.print(f"\n[green bold]✓ Config saved → {CONFIG_FILE}[/green bold]\n")
    return config
