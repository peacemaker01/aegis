# report/risk_scorer.py

SEVERITY_COLORS = {
    "CRITICAL": "bold red",
    "HIGH":     "red",
    "MEDIUM":   "yellow",
    "LOW":      "cyan",
    "INFO":     "dim",
}

SEVERITY_ICONS = {
    "CRITICAL": "🔴",
    "HIGH":     "🟠",
    "MEDIUM":   "🟡",
    "LOW":      "🔵",
    "INFO":     "⚪",
}

def risk_color(score: float) -> str:
    if score >= 8.0: return "bold red"
    if score >= 6.0: return "red"
    if score >= 4.0: return "yellow"
    if score >= 2.0: return "cyan"
    return "green"

def risk_label(score: float) -> str:
    if score >= 8.0: return "CRITICAL"
    if score >= 6.0: return "HIGH"
    if score >= 4.0: return "MEDIUM"
    if score >= 2.0: return "LOW"
    return "SAFE"

def recommendation_color(rec: str) -> str:
    return {"SAFE": "green", "CAUTION": "yellow", "AVOID": "red"}.get(rec, "white")

def bool_icon(val: bool, danger_if_true: bool = True) -> str:
    if danger_if_true:
        return "[red]YES ⚠[/red]" if val else "[green]NO ✓[/green]"
    else:
        return "[green]YES ✓[/green]" if val else "[red]NO ✗[/red]"
