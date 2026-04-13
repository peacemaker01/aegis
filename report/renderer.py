# report/renderer.py
import json
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.rule import Rule
from rich import box
from report.risk_scorer import (
    risk_color, risk_label, recommendation_color,
    bool_icon, SEVERITY_COLORS, SEVERITY_ICONS,
)

console = Console()


def render_audit(result: dict, address: str, chain: str) -> None:
    """Render the full audit report to the terminal."""
    score = result.get("risk_score", 0.0)
    rec = result.get("recommendation", "CAUTION")
    r_col = risk_color(score)
    r_lbl = risk_label(score)

    # ── Header ──────────────────────────────────────────────────────────────
    console.print()
    console.rule(f"[bold]Aegis — Contract Audit Report[/bold]", style="cyan")
    console.print(f"  [dim]Address:[/dim] {address}")
    console.print(f"  [dim]Chain:[/dim]   {chain.upper()}")
    console.print()

    # ── Risk Score ──────────────────────────────────────────────────────────
    score_bar = _score_bar(score)
    console.print(
        Panel(
            f"[{r_col}]{score:.1f} / 10[/{r_col}]  {score_bar}  "
            f"[bold {r_col}]{r_lbl}[/bold {r_col}]\n\n"
            f"Recommendation: [bold {recommendation_color(rec)}]{rec}[/bold {recommendation_color(rec)}]",
            title="[bold]Risk Score[/bold]",
            border_style=r_col,
            padding=(0, 2),
        )
    )
    console.print()

    # Show Slither impact if present
    slither_impact = result.get("_slither_impact")
    if slither_impact and slither_impact > 0:
        console.print(f"  [dim]⚠️ Slither static analysis added +{slither_impact:.1f} to risk score[/dim]")
        console.print()

    # ── Security Flags (compact table) ───────────────────────────────────────
    flags = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    flags.add_column("Flag", style="bold", width=28)
    flags.add_column("Value", width=12)

    # Show only the most important flags
    important_flags = [
        ("Honeypot", result.get("honeypot", False), True),
        ("Mint Function", result.get("mint_function", False), True),
        ("Owner Renounced", result.get("owner_renounced", False), False),
        ("Hidden Owner", result.get("hidden_owner", False), True),
        ("Blacklist", result.get("blacklist_function", False), True),
        ("Transfer Tax", result.get("transfer_tax_modifiable", False), True),
    ]
    for label, val, danger in important_flags:
        flags.add_row(label, bool_icon(val, danger_if_true=danger))

    console.print(Panel(flags, title="[bold]Security Flags[/bold]", border_style="blue"))
    console.print()

    # ── Key Findings (AI, compact) ──────────────────────────────────────────
    findings = result.get("findings", [])
    if findings:
        console.print("[bold]Key Findings[/bold]")
        for f in findings[:5]:  # Show only top 5
            sev = f.get("severity", "INFO")
            color = SEVERITY_COLORS.get(sev, "white")
            icon = SEVERITY_ICONS.get(sev, "•")
            console.print(
                f"  {icon} [{color}]{sev}[/{color}]  [bold]{f.get('title', '')}[/bold]"
            )
            desc = f.get('description', '')[:200]
            console.print(f"     {desc}...")
            console.print()
        if len(findings) > 5:
            console.print(f"  [dim]... and {len(findings) - 5} more findings[/dim]")
            console.print()

    # ── Slither Findings (organised) ─────────────────────────────────────────
    slither_findings = result.get("slither_findings", [])
    if slither_findings:
        _render_slither_findings(slither_findings)

    # ── Positive Signals ────────────────────────────────────────────────────
    positives = result.get("positive_signals", [])
    if positives:
        console.print("[bold green]Positive Signals[/bold green]")
        for p in positives[:5]:  # Show only top 5
            console.print(f"  [green]✓[/green] {p}")
        if len(positives) > 5:
            console.print(f"  [dim]... and {len(positives) - 5} more[/dim]")
        console.print()

    # ── Summary ─────────────────────────────────────────────────────────────
    if summary := result.get("summary"):
        console.print(
            Panel(summary, title="[bold]Summary[/bold]", border_style="dim")
        )
        console.print()

    console.rule(style="dim")


def _render_slither_findings(findings: list) -> None:
    """Clean, organised Slither findings renderer."""
    # Extract metadata
    metadata = None
    actual_findings = []
    for f in findings:
        if f.get("_slither_metadata"):
            metadata = f
        else:
            actual_findings.append(f)

    if not actual_findings:
        return

    console.print("[bold]Static Analysis (Slither)[/bold]")

    # Show summary bar
    if metadata and metadata.get("risk_impact"):
        impact = metadata["risk_impact"]
        counts = impact.get("counts", {})
        high = counts.get("HIGH", 0)
        medium = counts.get("MEDIUM", 0)
        low = counts.get("LOW", 0)

        # Create a compact summary line
        summary_parts = []
        if high:
            summary_parts.append(f"[bold red]{high} High[/bold red]")
        if medium:
            summary_parts.append(f"[yellow]{medium} Medium[/yellow]")
        if low:
            summary_parts.append(f"[cyan]{low} Low[/cyan]")

        if summary_parts:
            console.print(f"  {' • '.join(summary_parts)}")
        console.print(f"  [dim]Impact on risk score: +{impact['score_impact']:.1f}[/dim]")
        console.print()

    # Group findings by severity
    grouped = {"HIGH": [], "MEDIUM": [], "LOW": [], "INFORMATIONAL": [], "OPTIMIZATION": []}
    for f in actual_findings:
        sev = f.get("severity", "INFORMATIONAL")
        grouped.setdefault(sev, []).append(f)

    # Display HIGH and MEDIUM (most important)
    for severity in ["HIGH", "MEDIUM"]:
        sev_findings = grouped.get(severity, [])
        if not sev_findings:
            continue

        icon = "🔴" if severity == "HIGH" else "🟠"
        color = "bold red" if severity == "HIGH" else "yellow"
        console.print(f"  {icon} [{color}]{severity}[/{color}]")

        # Show only first 3 of each severity to avoid clutter
        for f in sev_findings[:3]:
            detector = f.get('detector', 'unknown')
            desc = f.get('description', '')[:100]
            line = f.get('line')

            console.print(f"    [bold]{detector}[/bold]")
            console.print(f"      {desc}...")
            if line:
                console.print(f"      [dim]→ Line {line}[/dim]")
            console.print()

        if len(sev_findings) > 3:
            console.print(f"    [dim]... and {len(sev_findings) - 3} more {severity.lower()} issues[/dim]")
        console.print()

    # Show LOW only if no HIGH/MEDIUM
    if not grouped.get("HIGH") and not grouped.get("MEDIUM"):
        low_findings = grouped.get("LOW", [])
        if low_findings:
            console.print(f"  🔵 [cyan]LOW[/cyan] ({len(low_findings)})")
            for f in low_findings[:3]:
                detector = f.get('detector', 'unknown')
                console.print(f"    [bold]{detector}[/bold]")
                if f.get('line'):
                    console.print(f"      [dim]↳ Line {f['line']}[/dim]")
            if len(low_findings) > 3:
                console.print(f"    [dim]... and {len(low_findings) - 3} more low issues[/dim]")
            console.print()

    # Summary line
    total = len(actual_findings)
    console.print(f"  [dim]Total: {total} findings[/dim]")
    console.print()


def render_spinner_start(address: str, chain: str, model: str) -> None:
    console.print(
        f"\n[cyan]⟳[/cyan]  Fetching [bold]{address[:10]}…[/bold] "
        f"on [bold]{chain.upper()}[/bold]"
    )
    console.print(f"[cyan]⟳[/cyan]  Running AI audit via [dim]{model}[/dim]\n")


def render_stream_token(token: str) -> None:
    """Print a streaming token without newline."""
    console.print(token, end="", highlight=False)


def render_error(msg: str) -> None:
    console.print(f"\n[red]✗ Error:[/red] {msg}\n")


def _score_bar(score: float, width: int = 20) -> str:
    filled = int((score / 10.0) * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"[dim]{bar}[/dim]"
