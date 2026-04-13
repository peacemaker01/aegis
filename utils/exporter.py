"""utils/exporter.py — Save reports to disk as JSON/text."""
import json
from datetime import datetime
from pathlib import Path

REPORTS_DIR = Path.home() / ".aegis" / "reports"


def export_report(contract: dict, audit: dict) -> Path:
    """Save a single audit report as JSON."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    addr    = contract.get("address", "unknown")[:12]
    chain   = contract.get("chain", "eth")
    fname   = f"audit_{chain}_{addr}_{ts}.json"
    path    = REPORTS_DIR / fname

    report = {
        "generated_at": datetime.now().isoformat(),
        "contract":     {
            k: v for k, v in contract.items()
            if k not in ("source", "abi")  # skip large fields
        },
        "audit": audit,
    }
    path.write_text(json.dumps(report, indent=2))
    return path


def export_batch_report(results: list) -> Path:
    """Save batch scan results as JSON."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    path  = REPORTS_DIR / f"batch_scan_{ts}.json"
    path.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "count":        len(results),
        "results":      results,
    }, indent=2))
    return path
