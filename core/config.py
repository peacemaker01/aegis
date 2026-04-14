# core/config.py
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt

from ai.models import MODELS, DEFAULT_MODEL_KEY
from core.api_keys import get_openrouter_key, get_etherscan_key

CONFIG_DIR   = Path.home() / ".aegis"
CONFIG_FILE  = CONFIG_DIR / "config.json"
LICENSE_FILE = CONFIG_DIR / "license.key"

console = Console()

# List of supported chains for RPC configuration
RPC_CHAINS = ["eth", "bsc", "polygon", "arb", "base", "op", "avax", "fantom", "zksync", "gnosis"]

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
        "infura": "",   # Legacy, can be used as fallback for RPC
    },
    "preferences": {
        "default_chain":  "eth",
        "stream_output":  True,
        "save_reports":   False,
        "cache_ttl_secs": 3600,
    },
    "notifications": {
        "telegram": {
            "enabled":    False,
            "bot_token":  "",
            "chat_id":    "",
        },
        "whatsapp": {
            "enabled":          False,
            "phone_number_id":  "",
            "access_token":     "",
            "from_number":      "",
            "to_number":        "",
        },
    },
    "rpc": {
        chain: "" for chain in RPC_CHAINS
    },
}


def _inject_baked_keys(config: dict) -> dict:
    """Inject built-in API keys when user hasn't provided their own."""
    if not config["openrouter"]["api_key"]:
        baked = get_openrouter_key()
        if baked:
            config["openrouter"]["api_key"] = baked
    if not config["explorers"]["etherscan"]:
        baked = get_etherscan_key()
        if baked:
            config["explorers"]["etherscan"] = baked
    return config


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        config = run_setup_wizard()
    else:
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            config = _deep_merge(DEFAULT_CONFIG, data)
        except (json.JSONDecodeError, OSError) as e:
            console.print(f"[red]Config error: {e}[/red]")
            sys.exit(1)
    return _inject_baked_keys(config)


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
    """Check that required API keys are available (built-in or user-provided)."""
    if not config["openrouter"]["api_key"]:
        console.print("[red]✗ AI service unavailable — contact support.[/red]")
        return False
    return True


def run_setup_wizard() -> dict:
    config = _deep_merge(DEFAULT_CONFIG, {})

    console.print("\n[bold cyan]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold cyan]")
    console.print("[bold cyan]  Aegis — First Time Setup[/bold cyan]")
    console.print("[bold cyan]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold cyan]\n")

    console.print("[bold]Step 1/3 — Default AI Model[/bold]\n")
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

    console.print("\n[bold]Step 2/3 — RPC Endpoints (optional)[/bold]")
    console.print("  Custom RPC endpoints enable deep analysis with Mythril.")
    console.print("  You can use public endpoints or your own (e.g., Infura, Alchemy).\n")
    set_rpc = Prompt.ask("  Set custom RPC endpoints now?", choices=["y","n"], default="n")
    if set_rpc == "y":
        for chain in RPC_CHAINS:
            rpc_val = Prompt.ask(f"    RPC URL for {chain.upper()} (Enter to skip)", default="")
            if rpc_val.strip():
                config["rpc"][chain] = rpc_val.strip()
        console.print("  [green]✓ RPC endpoints configured[/green]")
    else:
        console.print("  [dim]Skipped. You can set later with: aegis config set rpc.eth <URL>[/dim]")

    console.print("\n[bold]Step 3/3 — Notifications (optional)[/bold]")
    console.print("  You can configure Telegram or WhatsApp alerts for the watchlist monitor.")
    console.print("  You can skip this now and set later with:\n")
    console.print("    aegis config set notifications.telegram.enabled true")
    console.print("    aegis config set notifications.telegram.bot_token YOUR_BOT_TOKEN")
    console.print("    aegis config set notifications.telegram.chat_id YOUR_CHAT_ID\n")
    choice = Prompt.ask("  Set up Telegram notifications now?", choices=["y","n"], default="n")
    if choice == "y":
        bot_token = Prompt.ask("  Bot token (from @BotFather)", password=True)
        chat_id = Prompt.ask("  Chat ID (send a message to the bot, then visit getUpdates)")
        if bot_token and chat_id:
            config["notifications"]["telegram"]["enabled"] = True
            config["notifications"]["telegram"]["bot_token"] = bot_token.strip()
            config["notifications"]["telegram"]["chat_id"] = chat_id.strip()
            console.print("  [green]✓ Telegram notifications configured[/green]")
        else:
            console.print("  [yellow]⚠ Skipped[/yellow]")

    save_config(config)
    console.print(f"\n[green bold]✓ Config saved → {CONFIG_FILE}[/green bold]\n")
    return config