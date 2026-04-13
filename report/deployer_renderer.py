# report/deployer_renderer.py
"""Rich terminal renderer for deployer forensics reports."""
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.rule import Rule
from rich import box

from report.risk_scorer import risk_color, risk_label, bool_icon

console = Console()

VERDICT_COLORS = {
    "CLEAN":         "green",
    "SUSPICIOUS":    "yellow",
    "KNOWN_RUGGER":  "red",
    "SERIAL_RUGGER": "bold red",
}

VERDICT_ICONS = {
    "CLEAN":         "✅",
    "SUSPICIOUS":    "⚠️",
    "KNOWN_RUGGER":  "🚨",
    "SERIAL_RUGGER": "💀",
}

REC_COLORS = {
    "TRUST":     "green",
    "CAUTION":   "yellow",
    "AVOID":     "red",
    "BLACKLIST": "bold red",
}


def render_deployer_report(result: dict, profile: dict) -> None:
    score   = result.get("risk_score", 0.0)
    verdict = result.get("verdict", "SUSPICIOUS")
    r_col   = risk_color(score)
    v_col   = VERDICT_COLORS.get(verdict, "white")
    v_icon  = VERDICT_ICONS.get(verdict, "•")

    console.print()
    console.rule("[bold]Aegis — Deployer Forensics Report[/bold]", style="cyan")
    console.print(f"  [dim]Wallet:[/dim]  {profile['deployer']}")
    console.print(
        f"  [dim]Chains scanned:[/dim]  "
        f"{', '.join(profile.get('chains_scanned', []))}"
    )
    console.print()

    # ── Verdict Panel ─────────────────────────────────────────────────────────
    rec     = result.get("recommendation", "CAUTION")
    rec_col = REC_COLORS.get(rec, "white")
    score_bar = "█" * int((score / 10) * 20) + "░" * (20 - int((score / 10) * 20))

    console.print(Panel(
        f"[{r_col}]{score:.1f} / 10[/{r_col}]  [dim]{score_bar}[/dim]  "
        f"[{r_col}]{risk_label(score)}[/{r_col}]\n\n"
        f"Verdict:        [{v_col}]{v_icon} {verdict}[/{v_col}]\n"
        f"Recommendation: [bold {rec_col}]{rec}[/bold {rec_col}]",
        title="[bold]Risk Assessment[/bold]",
        border_style=r_col,
        padding=(0, 2),
    ))
    console.print()

    # ── Stats Row ─────────────────────────────────────────────────────────────
    stats = Table(box=box.SIMPLE, show_header=False, padding=(0, 3))
    stats.add_column("Label", style="dim", width=28)
    stats.add_column("Value", style="bold")

    funder = profile.get("funder", {})
    stats.add_row("Contracts deployed",  str(profile.get("total_deployments", 0)))
    stats.add_row("Chains active",       ", ".join(profile.get("chains_active", [])) or "none")
    stats.add_row("Funder wallet",       funder.get("funder_address", "unknown")[:20] + "…")
    stats.add_row("First funded",        funder.get("funding_date", "unknown"))
    stats.add_row("Chain hopping",       bool_icon(result.get("chain_hopping", False)))
    stats.add_row("Identity obfuscation",bool_icon(result.get("identity_obfuscation", False)))
    stats.add_row("Reuse pattern",       bool_icon(result.get("reuse_pattern", False)))
    stats.add_row("Estimated victims",   str(result.get("estimated_victims") or "unknown"))

    console.print(Panel(stats, title="[bold]Deployer Profile[/bold]", border_style="blue"))
    console.print()

    # ── Red Flags ─────────────────────────────────────────────────────────────
    flags = result.get("red_flags", [])
    if flags:
        console.print("[bold red]Red Flags[/bold red]")
        for f in flags:
            console.print(f"  [red]⚑[/red] {f}")
        console.print()

    # ── Findings ─────────────────────────────────────────────────────────────
    findings = result.get("findings", [])
    if findings:
        console.print("[bold]Findings[/bold]")
        sev_colors = {
            "CRITICAL": "bold red", "HIGH": "red",
            "MEDIUM": "yellow",     "LOW": "cyan", "INFO": "dim",
        }
        sev_icons = {
            "CRITICAL": "🔴", "HIGH": "🟠",
            "MEDIUM": "🟡",  "LOW": "🔵", "INFO": "⚪",
        }
        for f in findings:
            sev   = f.get("severity", "INFO")
            col   = sev_colors.get(sev, "white")
            icon  = sev_icons.get(sev, "•")
            console.print(
                f"  {icon} [{col}]{sev}[/{col}]  [bold]{f.get('title', '')}[/bold]"
            )
            console.print(f"     {f.get('description', '')}")
        console.print()

    # ── Deployment History Table ──────────────────────────────────────────────
    deployments = profile.get("deployments", [])
    if deployments:
        console.print("[bold]Deployment History[/bold]")
        t = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
        t.add_column("Date",     style="dim",  width=12)
        t.add_column("Chain",    style="cyan", width=8)
        t.add_column("Token",    width=18)
        t.add_column("Holders",  justify="right", width=8)
        t.add_column("Verified", width=10)
        t.add_column("Address",  style="dim", width=14)

        for d in deployments[:12]:
            name     = (d.get("token_name") or d.get("contract_name") or "—")[:16]
            holders  = d.get("holder_count", "?") or "?"
            verified = "[green]✓[/green]" if d.get("verified") else "[red]✗[/red]"
            addr     = d.get("contract_address", "")
            short_addr = addr[:6] + "…" + addr[-4:] if addr else "—"
            date_str   = d.get("date", "")[:10]
            t.add_row(date_str, d.get("chain","").upper(), name,
                      str(holders), verified, short_addr)

        console.print(t)
        if len(deployments) > 12:
            console.print(f"  [dim]... and {len(deployments)-12} more[/dim]")
        console.print()

    # ── Pattern + Summary ─────────────────────────────────────────────────────
    if pattern := result.get("pattern"):
        console.print(f"[bold]Behaviour Pattern:[/bold] {pattern}\n")

    if summary := result.get("summary"):
        console.print(Panel(
            summary,
            title="[bold]Summary[/bold]",
            border_style="dim",
        ))
    console.rule(style="dim")


def render_deployer_spinner(address: str, chains: list[str]) -> None:
    console.print(
        f"\n[cyan]⟳[/cyan]  Scanning deployer [bold]{address[:10]}…[/bold]"
    )
    console.print(
        f"[cyan]⟳[/cyan]  Chains: [dim]{', '.join(chains)}[/dim]"
    )
    console.print(
        "[cyan]⟳[/cyan]  Fetching deployment history (this takes ~10s)…\n"
    )
