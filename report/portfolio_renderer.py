# report/portfolio_renderer.py
"""Rich terminal renderers for wallet tracker + portfolio monitor."""
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.rule import Rule
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich import box

from report.risk_scorer import risk_color, risk_label, bool_icon

console = Console()

GRADE_COLORS = {"A": "green", "B": "cyan", "C": "yellow", "D": "red", "F": "bold red"}
ALERT_COLORS = {
    "NONE":     "dim",
    "INFO":     "cyan",
    "WARNING":  "yellow",
    "CRITICAL": "bold red",
}
ALERT_ICONS = {
    "NONE": "·", "INFO": "ℹ", "WARNING": "⚠", "CRITICAL": "🚨",
}


# ── Wallet Tracker ─────────────────────────────────────────────────────────────

def render_wallet_report(data: dict) -> None:
    snap     = data["wallet_snapshot"]
    audited  = data["audited_holdings"]
    analysis = data["portfolio_analysis"]
    skipped  = data["skipped_count"]

    wallet = snap["wallet"]
    chain  = snap["chain"].upper()

    console.print()
    console.rule("[bold]Aegis — Wallet Risk Report[/bold]", style="cyan")
    console.print(f"  [dim]Wallet:[/dim] {wallet}")
    console.print(f"  [dim]Chain:[/dim]  {chain}")
    console.print()

    # ── Portfolio Score Panel ──────────────────────────────────────────────
    score  = analysis.get("portfolio_risk_score", 0.0)
    grade  = analysis.get("risk_grade", "?")
    r_col  = risk_color(score)
    g_col  = GRADE_COLORS.get(grade, "white")
    bar    = "█" * int((score / 10) * 20) + "░" * (20 - int((score / 10) * 20))

    pct_high = analysis.get("pct_high_risk", 0.0)
    pct_safe = analysis.get("pct_safe", 0.0)

    console.print(Panel(
        f"Portfolio Risk Score: [{r_col}]{score:.1f} / 10[/{r_col}]  "
        f"[dim]{bar}[/dim]\n"
        f"Grade: [{g_col}]{grade}[/{g_col}]  ·  "
        f"High-risk exposure: [{r_col}]{pct_high:.0f}%[/{r_col}]  ·  "
        f"Safe: [green]{pct_safe:.0f}%[/green]",
        title="[bold]Portfolio Risk[/bold]",
        border_style=r_col,
        padding=(0, 2),
    ))
    console.print()

    # ── Holdings Table ─────────────────────────────────────────────────────
    console.print(f"[bold]Token Holdings[/bold]  "
                  f"[dim]({snap['token_count']} tokens, "
                  f"audited top {len(audited)})[/dim]")

    t = Table(box=box.SIMPLE, padding=(0, 1), show_header=True)
    t.add_column("#",        width=3,  justify="right")
    t.add_column("Token",    width=14, style="bold")
    t.add_column("Symbol",   width=8)
    t.add_column("Balance",  width=14, justify="right")
    t.add_column("Score",    width=7,  justify="center")
    t.add_column("Risk",     width=10)
    t.add_column("Rec",      width=10)
    t.add_column("Address",  width=14, style="dim")

    for i, h in enumerate(audited, 1):
        audit  = h.get("audit", {})
        score_ = audit.get("risk_score", 0.0) or 0.0
        rec    = audit.get("recommendation", "?")
        r_c    = risk_color(score_)
        rec_c  = {"SAFE": "green", "CAUTION": "yellow",
                  "AVOID": "red"}.get(rec, "white")
        name   = (h.get("token_name") or "?")[:12]
        sym    = (h.get("token_symbol") or "?")[:7]
        bal    = f"{h.get('balance', 0):,.4f}"
        addr   = h.get("contract_address", "")
        short  = addr[:6] + "…" + addr[-4:] if addr else "—"

        t.add_row(
            str(i), name, sym, bal,
            f"[{r_c}]{score_:.1f}[/{r_c}]",
            f"[{r_c}]{risk_label(score_)}[/{r_c}]",
            f"[{rec_c}]{rec}[/{rec_c}]",
            short,
        )

    console.print(t)

    if skipped > 0:
        console.print(
            f"  [dim]+ {skipped} more tokens not audited "
            f"(run aegis audit <address> --chain {snap['chain']} for full details)[/dim]"
        )
    console.print()

    # ── Portfolio Findings ─────────────────────────────────────────────────
    findings = analysis.get("findings", [])
    if findings:
        console.print("[bold]Portfolio Findings[/bold]")
        sev_c = {"CRITICAL": "bold red", "HIGH": "red",
                 "MEDIUM": "yellow",     "LOW": "cyan", "INFO": "dim"}
        sev_i = {"CRITICAL": "🔴", "HIGH": "🟠",
                 "MEDIUM": "🟡",  "LOW": "🔵", "INFO": "⚪"}
        for f in findings:
            s = f.get("severity", "INFO")
            console.print(
                f"  {sev_i.get(s,'•')} [{sev_c.get(s,'white')}]{s}[/{sev_c.get(s,'white')}]"
                f"  [bold]{f.get('title','')}[/bold]"
            )
            console.print(f"     {f.get('description','')}")
        console.print()

    # ── Recommendations ────────────────────────────────────────────────────
    recs = analysis.get("recommendations", [])
    if recs:
        console.print("[bold]Recommendations[/bold]")
        for r in recs:
            console.print(f"  [cyan]→[/cyan] {r}")
        console.print()

    # ── Summary ────────────────────────────────────────────────────────────
    if summary := analysis.get("summary"):
        console.print(Panel(summary, title="[bold]Summary[/bold]", border_style="dim"))
    console.rule(style="dim")


def render_wallet_spinner(wallet: str, chain: str) -> None:
    console.print(f"\n[cyan]⟳[/cyan]  Fetching holdings for [bold]{wallet[:12]}…[/bold] on [bold]{chain.upper()}[/bold]")
    console.print("[cyan]⟳[/cyan]  Auditing tokens (this may take 30-60s)…\n")


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
