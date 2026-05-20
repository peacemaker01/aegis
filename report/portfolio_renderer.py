# report/portfolio_renderer.py
"""Rich terminal renderers for portfolio monitor."""
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.rule import Rule
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich import box

from report.risk_scorer import risk_color, risk_label, bool_icon

console = Console()

ALERT_COLORS = {
    "NONE":     "dim",
    "INFO":     "cyan",
    "WARNING":  "yellow",
    "CRITICAL": "bold red",
}
ALERT_ICONS = {
    "NONE": "·", "INFO": "ℹ", "WARNING": "⚠", "CRITICAL": "🚨",
}


# ── Portfolio Monitor ──────────────────────────────────────────────────────────

def render_watchlist(entries: list[dict]) -> None:
    if not entries:
        console.print("\n[dim]Watchlist is empty. Add contracts with:[/dim]")
        console.print("  [bold]aegis watch add 0xABC --chain bsc[/bold]\n")
        return

    console.print(f"\n[bold]Watchlist[/bold]  [dim]({len(entries)} contracts)[/dim]\n")
    t = Table(box=box.SIMPLE, padding=(0, 1))
    t.add_column("Label",        width=20, style="bold")
    t.add_column("Chain",        width=8,  style="cyan")
    t.add_column("Address",      width=14, style="dim")
    t.add_column("Last Score",   width=10, justify="center")
    t.add_column("Last Verdict", width=12)
    t.add_column("Last Checked", width=16, style="dim")
    t.add_column("Threshold",    width=10, justify="center")

    for e in entries:
        score   = e.get("last_risk_score")
        verdict = e.get("last_verdict") or "—"
        addr    = e.get("address", "")
        short   = addr[:6] + "…" + addr[-4:] if addr else "—"
        checked = (e.get("last_checked") or "never")[:16]
        thresh  = str(e.get("alert_threshold", 6.0))

        if score is not None:
            r_c      = risk_color(score)
            score_s  = f"[{r_c}]{score:.1f}[/{r_c}]"
            v_c      = {"SAFE": "green", "CAUTION": "yellow",
                        "AVOID": "red"}.get(verdict, "white")
            verdict_s = f"[{v_c}]{verdict}[/{v_c}]"
        else:
            score_s  = "[dim]—[/dim]"
            verdict_s = "[dim]not checked[/dim]"

        t.add_row(
            e.get("label", "")[:18], e.get("chain","").upper(),
            short, score_s, verdict_s, checked, thresh,
        )
    console.print(t)


def render_monitor_results(results: list[dict]) -> None:
    """Print results from one monitor cycle."""
    alerts = [r for r in results if r.get("alert")]
    clean  = [r for r in results if not r.get("alert") and not r.get("error")]
    errors = [r for r in results if r.get("error")]

    now = __import__("datetime").datetime.now().strftime("%H:%M:%S")
    console.print(f"\n[dim]── Monitor cycle at {now} ──[/dim]")
    console.print(
        f"  Checked: [bold]{len(results)}[/bold]  "
        f"Alerts: [{'bold red' if alerts else 'dim'}]{len(alerts)}[/{'bold red' if alerts else 'dim'}]  "
        f"Clean: [green]{len(clean)}[/green]  "
        f"Errors: [dim]{len(errors)}[/dim]"
    )

    for a in alerts:
        level = a.get("alert_level", "WARNING")
        icon  = ALERT_ICONS.get(level, "⚠")
        color = ALERT_COLORS.get(level, "yellow")
        console.print(
            f"\n  {icon} [{color}]{level}[/{color}]  "
            f"[bold]{a.get('label', a.get('address','')[:10])}[/bold]  "
            f"[{a.get('chain','').upper()}]"
        )
        console.print(f"     {a.get('message', '')}")
        if a.get("old_score") is not None:
            old_c = risk_color(a["old_score"])
            new_c = risk_color(a["new_score"])
            console.print(
                f"     Score: [{old_c}]{a['old_score']:.1f}[/{old_c}]"
                f" → [{new_c}]{a['new_score']:.1f}[/{new_c}]"
            )
        action = a.get("action", "NONE")
        if action != "NONE":
            a_c = "bold red" if action == "SELL_IMMEDIATELY" else "yellow"
            console.print(f"     Action: [{a_c}]{action}[/{a_c}]")
        for change in a.get("changes", [])[:3]:
            console.print(f"     [dim]• {change}[/dim]")

    for e in errors:
        console.print(
            f"\n  [red]✗[/red]  {e.get('label', e.get('address','')[:10])}  "
            f"[dim]{e.get('error', '')}[/dim]"
        )

    if not alerts:
        console.print("  [green]✓[/green]  All contracts within normal parameters")


def render_monitor_spinner(count: int, interval: int) -> None:
    console.print(
        f"\n[cyan]⟳[/cyan]  Monitoring [bold]{count}[/bold] contracts "
        f"every [bold]{interval}[/bold] minutes"
    )
    console.print("[dim]  Press Ctrl+C to stop[/dim]\n")