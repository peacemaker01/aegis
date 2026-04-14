# analysis/slither_integration.py
"""
Slither integration for Solidity source code analysis.
Handles multi-file contracts with proper import resolution.
"""

import sys
import os
import shutil
import inspect
import re
import tempfile
import subprocess
import traceback
from pathlib import Path
from typing import Optional

# Only import Slither if available (graceful fallback)
try:
    from slither.slither import Slither
    from slither.detectors.abstract_detector import AbstractDetector
    import slither.detectors.all_detectors as _all_detectors
    SLITHER_AVAILABLE = True
except ImportError:
    SLITHER_AVAILABLE = False

# ──────────────────────────────────────────────────────────────
# Locate solc binary (cross-platform)
# ──────────────────────────────────────────────────────────────

def _find_solc() -> Optional[str]:
    """Find solc binary on the system."""
    # 1. Check system PATH
    system_solc = shutil.which('solc')
    if system_solc:
        return system_solc
    
    # 2. Check for bundled solc (PyInstaller)
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
        bundled = os.path.join(base, 'solc')
        if os.path.exists(bundled):
            return bundled
    
    # 3. Check for wrapper script in project root
    wrapper = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'solc-wrapper.sh')
    if os.path.exists(wrapper):
        return wrapper
    
    # 4. Check common Termux locations
    termux_paths = [
        '/data/data/com.termux/files/usr/bin/solc',
        os.path.expanduser('~/../usr/bin/solc')
    ]
    for p in termux_paths:
        if os.path.exists(p):
            return p
    
    return None

SOLC_BINARY = _find_solc()

# ──────────────────────────────────────────────────────────────
# Detector registration
# ──────────────────────────────────────────────────────────────

def _register_all_detectors(slither_instance) -> int:
    """Register every built-in detector."""
    if not SLITHER_AVAILABLE:
        return 0
    count = 0
    for _name, cls in inspect.getmembers(_all_detectors, inspect.isclass):
        if cls is not AbstractDetector and issubclass(cls, AbstractDetector):
            try:
                slither_instance.register_detector(cls)
                count += 1
            except Exception:
                pass
    return count

# ──────────────────────────────────────────────────────────────
# File writing and contract selection
# ──────────────────────────────────────────────────────────────

def _write_sources(source, tmp_path: Path, debug: bool) -> list[Path]:
    """Write source to tmpdir."""
    written = []
    
    if isinstance(source, dict):
        if debug:
            print(f"[DEBUG] Writing {len(source)} files to {tmp_path}")
        for filename, content in source.items():
            safe = filename.replace("..", "_").lstrip("/").lstrip("\\")
            if not safe:
                continue
            file_path = tmp_path / safe
            try:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(
                    content if isinstance(content, str) else str(content),
                    encoding="utf-8",
                )
                written.append(file_path)
                if debug:
                    print(f"[DEBUG]   Wrote: {safe}")
            except Exception as exc:
                if debug:
                    print(f"[DEBUG]   Failed to write {safe}: {exc}")
    else:
        file_path = tmp_path / "contract.sol"
        file_path.write_text(source, encoding="utf-8")
        written.append(file_path)
        if debug:
            print("[DEBUG] Wrote single file: contract.sol")
    
    return written

def _score_candidate(path: Path, tmp_path: Path) -> int:
    """Score a contract file for selection."""
    score = 0
    stem = path.stem
    try:
        rel_parts = path.relative_to(tmp_path).parts
    except ValueError:
        return -1000
    
    # Penalize vendor paths
    if any(p in ("node_modules", "lib", "vendor") for p in rel_parts):
        score -= 100
    if rel_parts and rel_parts[0].startswith("@"):
        score -= 100
    
    # Penalize interfaces and libraries
    if re.match(r'^I[A-Z]', stem):
        score -= 20
    if stem.startswith("Abstract") or stem.startswith("Base"):
        score -= 10
    if stem.lower() in ("migrations", "truffle-config", "hardhat.config"):
        score -= 50
    
    # Prefer contracts with contract keyword
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r'\bcontract\s+\w+', content):
            score += 30
        if re.search(r'\binterface\s+\w+', content) and not re.search(r'\bcontract\s+\w+', content):
            score -= 15
        if re.search(r'\blibrary\s+\w+', content) and not re.search(r'\bcontract\s+\w+', content):
            score -= 10
    except Exception:
        pass
    
    return score

def _find_main_sol(tmp_path: Path, debug: bool) -> Optional[Path]:
    """Find the main contract file."""
    all_sol = list(tmp_path.rglob("*.sol"))
    if not all_sol:
        return None
    scored = sorted(all_sol, key=lambda p: _score_candidate(p, tmp_path), reverse=True)
    if debug and scored:
        print(f"[DEBUG]   Selected: {scored[0].relative_to(tmp_path)}")
    return scored[0] if scored else None

# ──────────────────────────────────────────────────────────────
# Compilation with fallback strategies
# ──────────────────────────────────────────────────────────────

def _try_compile(main_file: Path, tmp_path: Path, debug: bool) -> Optional:
    """Try to compile with various strategies."""
    if not SLITHER_AVAILABLE:
        if debug:
            print("[DEBUG] Slither not installed")
        return None
    
    if not SOLC_BINARY:
        if debug:
            print("[DEBUG] solc not found")
        return None
    
    try:
        os.chmod(SOLC_BINARY, 0o755)
    except OSError:
        pass
    
    # Try different compilation strategies
    strategies = [
        dict(solc=SOLC_BINARY),
        dict(),  # Auto-detect
    ]
    
    for i, kwargs in enumerate(strategies, 1):
        if debug:
            print(f"[DEBUG] Compilation strategy {i}")
        try:
            slither = Slither(str(main_file), **kwargs)
            if debug:
                print(f"[DEBUG] Strategy {i} succeeded")
            return slither
        except Exception as exc:
            if debug:
                msg = str(exc)[:200]
                print(f"[DEBUG] Strategy {i} failed: {msg}")
    
    if debug:
        print("[DEBUG] All compilation strategies exhausted")
    return None

# ──────────────────────────────────────────────────────────────
# Finding extraction and parsing
# ──────────────────────────────────────────────────────────────

def _extract_findings(results: list, debug: bool) -> list[dict]:
    """Extract findings from Slither results."""
    findings = []
    for item in results:
        batch = item if isinstance(item, list) else [item]
        for r in batch:
            if not hasattr(r, "get"):
                continue
            finding = {
                "source": "slither",
                "detector": r.get("check", "unknown"),
                "severity": str(r.get("impact", "UNKNOWN")).upper(),
                "confidence": str(r.get("confidence", "UNKNOWN")).upper(),
                "title": r.get("check", "unknown"),
                "description": r.get("description", "").strip(),
                "line": None,
                "filename": None,
            }
            elements = r.get("elements") or []
            if elements and isinstance(elements[0], dict):
                elem = elements[0]
                sm = elem.get("source_mapping") or {}
                lines = sm.get("lines") or []
                if lines:
                    finding["line"] = lines[0]
                fname = sm.get("filename_relative") or sm.get("filename_short") or ""
                if fname:
                    finding["filename"] = fname
            findings.append(finding)
            if debug:
                loc = f"line {finding['line']}" if finding["line"] else "N/A"
                print(f"[DEBUG]   {finding['severity']}: {finding['detector']} ({loc})")
    return findings

# ──────────────────────────────────────────────────────────────
# Metadata with severity filtering
# ──────────────────────────────────────────────────────────────

def _compute_metadata(findings: list) -> dict:
    """
    Compute metadata for Slither findings.
    Only HIGH, MEDIUM, LOW severities contribute to risk score.
    INFORMATIONAL and OPTIMIZATION are excluded from score impact.
    """
    severity_weights = {"HIGH": 2.5, "MEDIUM": 1.0, "LOW": 0.3}
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "INFORMATIONAL": 0, "OPTIMIZATION": 0}
    
    for f in findings:
        sev = f.get("severity", "INFORMATIONAL")
        if sev in counts:
            counts[sev] += 1
    
    # Only HIGH, MEDIUM, LOW contribute to risk impact
    score_impact = sum(
        counts[sev] * weight for sev, weight in severity_weights.items()
    )
    
    return {
        "_slither_metadata": True,
        "risk_impact": {
            "score_impact": round(score_impact, 2),
            "counts": counts,
            "has_critical": counts["HIGH"] > 0,
            "summary": f"{counts['HIGH']} High, {counts['MEDIUM']} Medium, {counts['LOW']} Low issues",
        },
        "total_findings": len(findings),
    }

# ──────────────────────────────────────────────────────────────
# Core runner
# ──────────────────────────────────────────────────────────────

def run_slither_on_source(source, debug: bool = False) -> list:
    """Run Slither on Solidity source and return findings."""
    if not source:
        return []
    
    if not SLITHER_AVAILABLE:
        if debug:
            print("[DEBUG] Slither not installed. Install with: pip install slither-analyzer")
        return []
    
    if not SOLC_BINARY:
        if debug:
            print("[DEBUG] solc not found. Install with: sudo apt install solc")
        return []
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        # Write files
        written = _write_sources(source, tmp_path, debug)
        if not written:
            if debug:
                print("[DEBUG] No files written")
            return []
        
        # Pick main file
        if debug:
            print(f"[DEBUG] Selecting main contract")
        main_file = _find_main_sol(tmp_path, debug)
        if not main_file:
            if debug:
                print("[DEBUG] Could not find main .sol file")
            return []
        
        # Attempt compilation
        slither = _try_compile(main_file, tmp_path, debug)
        if slither is None:
            return []
        
        # Register detectors
        n = _register_all_detectors(slither)
        if debug:
            print(f"[DEBUG] Registered {n} detectors")
        
        # Run detectors
        try:
            raw_results = slither.run_detectors()
        except Exception as exc:
            if debug:
                print(f"[DEBUG] Detector execution failed: {exc}")
            return []
        
        # Extract findings
        findings = _extract_findings(raw_results, debug)
        
        # Add metadata
        if findings:
            findings.append(_compute_metadata(findings))
        
        if debug:
            print(f"[DEBUG] Found {len(findings) - 1} issues (plus metadata)")
        
        return findings