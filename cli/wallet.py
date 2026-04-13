"""
cli/wallet.py
Wallet forensics — analyze a wallet's on-chain behavior.
"""
from rich.console import Console

from fetchers.etherscan import (
    get_native_balance, get_tx_list, CHAINS
)
from ai.client import OpenRouterClient, OpenRouterError
from ai.prompt_builder import build_wallet_prompt
from report.renderer import render_wallet_report, render_error, render_warning
from utils.validators import is_valid_address
from core.config import validate_config

console = Console()


async def wallet_command(
    address: str,
    chain:   str,
    config:  dict,
):
    """Analyze a wallet's transaction history and risk profile."""

    issues = validate_config(config)
    for issue in issues:
        if issue.startswith("❌"):
            render_error(issue)
            return
        render_warning(issue)

    if not is_valid_address(address):
        render_error(f"Invalid address: {address}")
        return

    if chain not in CHAINS:
        render_error(f"Unknown chain: {chain}")
        return

    chain_id   = CHAINS[chain]["id"]
    chain_name = CHAINS[chain]["name"]
    api_key    = config["explorers"].get("etherscan", "")

    console.print(
        f"\n[bold cyan]🔎 Wallet Analysis[/bold cyan] "
        f"[dim]{address[:10]}...{address[-8:]}[/dim] "
        f"on [cyan]{chain_name}[/cyan]\n"
    )

    with console.status("[bold cyan]Fetching wallet data...[/]", spinner="dots"):
        balance = await get_native_balance(address, chain_id, api_key)
        txs     = await get_tx_list(address, chain_id, api_key, limit=30)

    native_symbols = {"eth": "ETH", "bsc": "BNB", "polygon": "MATIC",
                      "arb": "ETH", "base": "ETH", "op": "ETH"}

    wallet = {
        "address":       address,
        "chain":         chain,
        "chain_id":      chain_id,
        "chain_name":    chain_name,
        "balance":       balance,
        "native_symbol": native_symbols.get(chain, "ETH"),
        "transactions":  txs,
    }

    console.print(
        f"[dim]Balance: {balance} {wallet['native_symbol']}  "
        f"Transactions fetched: {len(txs)}[/dim]\n"
    )

    # AI analysis
    messages = build_wallet_prompt(wallet)

    or_client = OpenRouterClient(
        api_key    = config["openrouter"]["api_key"],
        model      = config["openrouter"]["model"],
        max_tokens = 2000,
    )

    with console.status("[bold cyan]Running AI analysis...[/]", spinner="dots"):
        try:
            analysis = await or_client.complete(messages)
        except OpenRouterError as e:
            render_error(f"AI error: {e}")
            return

    render_wallet_report(wallet, analysis)
