# analysis/slither_integration.py
"""
Enhanced Slither integration for Solidity source code analysis.
Extracts detailed vulnerability data, filters noise, and generates human-readable summaries.
"""
import sys
import os
import shutil
import inspect
import re
import tempfile
import subprocess
import json
from pathlib import Path
from typing import Optional, List, Dict, Any

try:
    from slither.slither import Slither
    from slither.detectors.abstract_detector import AbstractDetector
    import slither.detectors.all_detectors as _all_detectors
    SLITHER_AVAILABLE = True
except ImportError:
    SLITHER_AVAILABLE = False

# ----------------------------------------------------------------------
# Solc version management
# ----------------------------------------------------------------------
def _find_solc() -> Optional[str]:
    return shutil.which('solc')

SOLC_BINARY = _find_solc()

def _detect_required_solc(source_files: list) -> Optional[str]:
    version_pattern = re.compile(r'pragma solidity\s+([^;]+);', re.IGNORECASE)
    for f in source_files:
        try:
            content = Path(f).read_text(encoding='utf-8', errors='ignore')
            match = version_pattern.search(content)
            if match:
                version_spec = match.group(1).strip()
                ver_match = re.search(r'(\d+\.\d+\.\d+)', version_spec)
                if ver_match:
                    return ver_match.group(1)
        except Exception:
            continue
    return None

def _switch_solc_version(version: str, debug: bool = False) -> bool:
    try:
        if not shutil.which('solc-select'):
            if debug:
                print("[DEBUG] solc-select not found.")
            return False
        subprocess.run(['solc-select', 'install', version], capture_output=True, timeout=60)
        subprocess.run(['solc-select', 'use', version], capture_output=True, timeout=10)
        if debug:
            print(f"[DEBUG] Switched solc to version {version}")
        return True
    except Exception as e:
        if debug:
            print(f"[DEBUG] Failed to switch solc version: {e}")
        return False

# ----------------------------------------------------------------------
# Detector registration
# ----------------------------------------------------------------------
def _register_all_detectors(slither_instance) -> int:
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

# ----------------------------------------------------------------------
# File writing and contract selection
# ----------------------------------------------------------------------
def _write_sources(source, tmp_path: Path, debug: bool) -> List[Path]:
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
    score = 0
    stem = path.stem
    try:
        rel_parts = path.relative_to(tmp_path).parts
    except ValueError:
        return -1000
    if any(p in ("node_modules", "lib", "vendor") for p in rel_parts):
        score -= 100
    if rel_parts and rel_parts[0].startswith("@"):
        score -= 100
    if re.match(r'^I[A-Z]', stem):
        score -= 20
    if stem.startswith("Abstract") or stem.startswith("Base"):
        score -= 10
    if stem.lower() in ("migrations", "truffle-config", "hardhat.config"):
        score -= 50
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
    all_sol = list(tmp_path.rglob("*.sol"))
    if not all_sol:
        return None
    scored = sorted(all_sol, key=lambda p: _score_candidate(p, tmp_path), reverse=True)
    if debug and scored:
        print(f"[DEBUG]   Selected: {scored[0].relative_to(tmp_path)}")
    return scored[0] if scored else None

# ----------------------------------------------------------------------
# Compilation
# ----------------------------------------------------------------------
def _try_compile(main_file: Path, tmp_path: Path, debug: bool) -> Optional[Slither]:
    if not SLITHER_AVAILABLE:
        return None
    if not SOLC_BINARY:
        return None
    strategies = [dict(solc=SOLC_BINARY), dict()]
    for i, kwargs in enumerate(strategies, 1):
        try:
            slither = Slither(str(main_file), **kwargs)
            if debug:
                print(f"[DEBUG] Compilation strategy {i} succeeded")
            return slither
        except Exception as exc:
            if debug:
                print(f"[DEBUG] Strategy {i} failed: {str(exc)[:200]}")
    return None

# ----------------------------------------------------------------------
# Printer execution
# ----------------------------------------------------------------------
def _run_printer(slither: Slither, printer_name: str, debug: bool = False) -> str:
    """Execute a Slither printer and return its output as a string."""
    try:
        from slither.printers.abstract_printer import AbstractPrinter
        import slither.printers.all_printers as printers_module
        
        printer_class = None
        for _name, cls in inspect.getmembers(printers_module, inspect.isclass):
            if issubclass(cls, AbstractPrinter) and cls != AbstractPrinter:
                if cls.ARGUMENT == printer_name:
                    printer_class = cls
                    break
        
        if not printer_class:
            if debug:
                print(f"[DEBUG] Printer '{printer_name}' not found")
            return ""
        
        printer_instance = printer_class(slither, logger=None)
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.md', delete=False) as f:
            temp_path = f.name
        printer_instance.output(temp_path)
        output = Path(temp_path).read_text()
        os.unlink(temp_path)
        return output
    except Exception as e:
        if debug:
            print(f"[DEBUG] Printer '{printer_name}' failed: {e}")
        return ""

# ----------------------------------------------------------------------
# Enhanced finding extraction with filtering
# ----------------------------------------------------------------------
# Detectors we ALWAYS keep (security-critical)
KEEP_DETECTORS = {
    "reentrancy-eth", "reentrancy-no-eth", "reentrancy-unlimited-gas",
    "unchecked-lowlevel", "arbitrary-send-erc20", "arbitrary-send-eth",
    "suicidal", "tx-origin", "incorrect-equality", "locked-ether",
    "controlled-delegatecall", "uninitialized-state", "uninitialized-local",
    "calls-loop", "timestamp", "divide-before-multiply", "shadowing-state",
    "unprotected-upgrade", "missing-zero-check", "unused-return"
}

# Detectors we EXPLICITLY IGNORE (noise)
IGNORE_DETECTORS = {
    "dead-code", "solc-version", "naming-convention", "pragma",
    "unused-state", "external-function", "constable-states",
    "redundant-statements", "incorrect-modifier", "public-mappings-nested"
}

def _should_keep_finding(finding: Dict[str, Any], deep_scan: bool = False) -> bool:
    """Determine if a finding is valuable enough to keep."""
    detector = finding.get("detector", "")
    impact = finding.get("severity", "")
    
    # Always keep HIGH and MEDIUM
    if impact in ("HIGH", "MEDIUM"):
        return True
    
    # Explicitly ignore known noise detectors
    if detector in IGNORE_DETECTORS:
        return False
    
    # In deep scan mode, keep all LOW severity findings except explicit noise
    if deep_scan:
        return True
    
    # In fast mode, only keep specific LOW detectors we care about
    return detector in KEEP_DETECTORS

def _extract_findings(results: list, debug: bool, deep_scan: bool = False) -> List[Dict[str, Any]]:
    findings = []
    for item in results:
        batch = item if isinstance(item, list) else [item]
        for r in batch:
            if not hasattr(r, "get"):
                continue
            detector = r.get("check", "unknown")
            impact = str(r.get("impact", "UNKNOWN")).upper()
            confidence = str(r.get("confidence", "UNKNOWN")).upper()
            description = r.get("description", "").strip()
            
            elements = r.get("elements") or []
            locations = []
            for elem in elements:
                if isinstance(elem, dict):
                    loc = {
                        "type": elem.get("type", ""),
                        "name": elem.get("name", ""),
                        "line": None,
                        "filename": None,
                    }
                    sm = elem.get("source_mapping") or {}
                    lines = sm.get("lines") or []
                    if lines:
                        loc["line"] = lines[0]
                    fname = sm.get("filename_relative") or sm.get("filename_short") or ""
                    if fname:
                        loc["filename"] = fname
                    locations.append(loc)
            
            finding = {
                "source": "slither",
                "detector": detector,
                "severity": impact,
                "confidence": confidence,
                "title": detector.replace("-", " ").title(),
                "description": description,
                "locations": locations,
                "swc_id": _map_detector_to_swc(detector),
            }
            
            if _should_keep_finding(finding, deep_scan):
                findings.append(finding)
                if debug:
                    line_info = locations[0]["line"] if locations else "N/A"
                    print(f"[DEBUG]   [KEPT] {impact}: {detector} ({line_info})")
            elif debug:
                line_info = locations[0]["line"] if locations else "N/A"
                print(f"[DEBUG]   [FILTERED] {impact}: {detector} ({line_info})")
    
    return findings

def _map_detector_to_swc(detector: str) -> str:
    mapping = {
        "reentrancy-eth": "SWC-107",
        "reentrancy-no-eth": "SWC-107",
        "unchecked-lowlevel": "SWC-104",
        "arbitrary-send-erc20": "SWC-105",
        "suicidal": "SWC-106",
        "tx-origin": "SWC-115",
        "timestamp": "SWC-116",
        "incorrect-equality": "SWC-123",
        "locked-ether": "SWC-108",
        "uninitialized-state": "SWC-109",
        "calls-loop": "SWC-113",
    }
    return mapping.get(detector, "")

def _compute_metadata(findings: list) -> dict:
    severity_weights = {"HIGH": 2.5, "MEDIUM": 1.0, "LOW": 0.3}
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "INFORMATIONAL": 0, "OPTIMIZATION": 0}
    for f in findings:
        sev = f.get("severity", "INFORMATIONAL")
        if sev in counts:
            counts[sev] += 1
    score_impact = sum(counts[sev] * severity_weights.get(sev, 0) for sev in counts)
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

# ----------------------------------------------------------------------
# Core runner
# ----------------------------------------------------------------------
def run_slither_on_source(source, debug: bool = False, deep_scan: bool = False) -> list:
    if not source:
        return []
    if not SLITHER_AVAILABLE:
        if debug:
            print("[DEBUG] Slither not installed.")
        return []
    if not SOLC_BINARY:
        if debug:
            print("[DEBUG] solc not found.")
        return []
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        written = _write_sources(source, tmp_path, debug)
        if not written:
            return []
        
        required_version = _detect_required_solc(written)
        if required_version:
            _switch_solc_version(required_version, debug)
        
        main_file = _find_main_sol(tmp_path, debug)
        if not main_file:
            return []
        
        slither = _try_compile(main_file, tmp_path, debug)
        if slither is None:
            return []
        
        _register_all_detectors(slither)
        
        # Run detectors
        try:
            raw_results = slither.run_detectors()
        except Exception as exc:
            if debug:
                print(f"[DEBUG] Detector execution failed: {exc}")
            raw_results = []
        
        findings = _extract_findings(raw_results, debug, deep_scan=deep_scan)
        
        # Run valuable printers
        human_summary = _run_printer(slither, "human-summary", debug)
        vars_auth = _run_printer(slither, "vars-and-auth", debug)
        
        if human_summary:
            findings.append({"_slither_human_summary": human_summary})
        if vars_auth:
            findings.append({"_slither_vars_and_auth": vars_auth})
        
        if findings:
            findings.append(_compute_metadata(findings))
        
        return findings