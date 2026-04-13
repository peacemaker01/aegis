# analysis/slither_integration.py
import sys
import os
import stat
import shutil
import inspect
import re
import tempfile
import subprocess
import traceback
from pathlib import Path
from typing import Optional

from slither.slither import Slither
from slither.detectors.abstract_detector import AbstractDetector
import slither.detectors.all_detectors as _all_detectors

# ──────────────────────────────────────────────────────────────
# Locate and fix permissions for bundled files
# ──────────────────────────────────────────────────────────────

def _get_bundled_file(filename: str) -> Optional[str]:
    """Return path to a file bundled with PyInstaller, or None."""
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
        candidate = os.path.join(base, filename)
        if os.path.exists(candidate):
            return candidate
    # Development mode
    if os.path.exists(filename):
        return os.path.abspath(filename)
    return None

def _ensure_executable_copy(src_path: str, dst_dir: str) -> Optional[str]:
    """Copy src to dst_dir, make it executable, return dst path."""
    if not src_path or not os.path.exists(src_path):
        return None
    dst = os.path.join(dst_dir, os.path.basename(src_path))
    shutil.copy2(src_path, dst)
    try:
        os.chmod(dst, os.stat(dst).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        subprocess.run(['chmod', '+x', dst], capture_output=True)
    return dst

# Global variables for binaries
_WRAPPER_PATH = None
_SOLC_BINARY = None

def _init_binaries(debug: bool = False) -> bool:
    global _WRAPPER_PATH, _SOLC_BINARY
    bundled_wrapper = _get_bundled_file("solc-wrapper.sh")
    bundled_solc = _get_bundled_file("solc")
    if not bundled_wrapper or not bundled_solc:
        if debug:
            print("[DEBUG] Bundled solc or wrapper not found")
        return False
    temp_dir = tempfile.mkdtemp(prefix="aegis_slither_")
    _WRAPPER_PATH = _ensure_executable_copy(bundled_wrapper, temp_dir)
    _SOLC_BINARY = _ensure_executable_copy(bundled_solc, temp_dir)
    if debug:
        print(f"[DEBUG] Using solc wrapper: {_WRAPPER_PATH}")
        print(f"[DEBUG] Using solc binary: {_SOLC_BINARY}")
    return bool(_WRAPPER_PATH and _SOLC_BINARY)

# ──────────────────────────────────────────────────────────────
# Detector registration
# ──────────────────────────────────────────────────────────────

def _register_all_detectors(slither_instance: Slither) -> int:
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
# File writing
# ──────────────────────────────────────────────────────────────

def _write_sources(source, tmp_path: Path, debug: bool) -> list[Path]:
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
                file_path.write_text(content if isinstance(content, str) else str(content), encoding="utf-8")
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

# ──────────────────────────────────────────────────────────────
# Main contract selection
# ──────────────────────────────────────────────────────────────

_VENDOR_DIR_PREFIXES = ("@", "node_modules", "lib", "deps", "vendor", "packages")
_SKIP_STEMS = frozenset({"migrations", "truffle-config", "hardhat.config"})

def _is_vendor_path(path: Path, tmp_path: Path) -> bool:
    try:
        parts = path.relative_to(tmp_path).parts
    except ValueError:
        return False
    if not parts:
        return False
    top = parts[0]
    return any(top.startswith(p) for p in _VENDOR_DIR_PREFIXES)

def _score_candidate(path: Path, tmp_path: Path) -> int:
    score = 0
    stem = path.stem
    rel_parts = path.relative_to(tmp_path).parts
    if _is_vendor_path(path, tmp_path):
        score -= 100
    if re.match(r'^I[A-Z]', stem):
        score -= 20
    if stem.startswith("Abstract") or stem.startswith("Base"):
        score -= 10
    if stem.lower() in _SKIP_STEMS:
        score -= 50
    score -= len(rel_parts) * 2
    if any(p in ("src", "contracts") for p in rel_parts):
        score += 15
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
    if debug:
        for p in scored[:5]:
            print(f"[DEBUG]   Candidate score {_score_candidate(p, tmp_path):+d}: {p.relative_to(tmp_path)}")
    return scored[0]

# ──────────────────────────────────────────────────────────────
# Import scanning and remappings
# ──────────────────────────────────────────────────────────────

_IMPORT_RE = re.compile(r"""import\s+(?:[^"']*?)\s*['"]([^'"]+)['"]""", re.MULTILINE)

def _scan_imports(tmp_path: Path) -> set[str]:
    imports = set()
    for sol in tmp_path.rglob("*.sol"):
        try:
            content = sol.read_text(encoding="utf-8", errors="ignore")
            for m in _IMPORT_RE.finditer(content):
                imports.add(m.group(1))
        except Exception:
            pass
    return imports

def _build_remappings(tmp_path: Path, debug: bool) -> list[str]:
    all_imports = _scan_imports(tmp_path)
    namespaced = [i for i in all_imports if i.startswith("@")]
    if not namespaced and debug:
        print("[DEBUG] No @-namespace imports detected")
    namespaces = {}
    for imp in namespaced:
        parts = imp.split("/")
        ns = parts[0]
        rest = parts[1:]
        if rest:
            namespaces.setdefault(ns, []).append("/".join(rest))
    remappings = {}
    for ns, sub_paths in namespaces.items():
        if ns in remappings:
            continue
        resolved = None
        for sub in sub_paths:
            sub_parts = sub.split("/")
            filename = sub_parts[-1]
            for candidate in tmp_path.rglob(filename):
                try:
                    rel = candidate.relative_to(tmp_path)
                    rel_parts = list(rel.parts)
                    if rel_parts[-len(sub_parts):] == sub_parts:
                        root_parts = rel_parts[:-len(sub_parts)]
                        root = tmp_path.joinpath(*root_parts) if root_parts else tmp_path
                        resolved = str(root)
                        break
                except Exception:
                    continue
            if resolved:
                break
        if resolved:
            remappings[ns] = resolved
            if debug:
                print(f"[DEBUG] Remapping: {ns}={resolved}")
        else:
            if debug:
                print(f"[DEBUG] Could not resolve remapping for {ns}")
    return [f"{ns}={path}" for ns, path in remappings.items()]

def _build_allow_paths(tmp_path: Path) -> str:
    return str(tmp_path)

# ──────────────────────────────────────────────────────────────
# Finding extraction
# ──────────────────────────────────────────────────────────────

def _extract_findings(results: list, debug: bool) -> list[dict]:
    findings = []
    for item in results:
        batch = item if isinstance(item, list) else [item]
        for r in batch:
            if not hasattr(r, "get"):
                if debug:
                    print(f"[DEBUG] Unexpected result type: {type(r)}")
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
            if elements:
                elem = elements[0]
                if isinstance(elem, dict):
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
                print(f"[DEBUG]   {finding['severity']}: {finding['detector']} ({loc}) — {finding['description'][:80]}")
    return findings

# ──────────────────────────────────────────────────────────────
# Severity scoring
# ──────────────────────────────────────────────────────────────

_SEVERITY_WEIGHTS = {"HIGH": 2.5, "MEDIUM": 1.0, "LOW": 0.3, "INFORMATIONAL": 0.0, "OPTIMIZATION": 0.0}

def _compute_metadata(findings: list[dict]) -> dict:
    counts = {k: 0 for k in _SEVERITY_WEIGHTS}
    for f in findings:
        sev = f.get("severity", "INFORMATIONAL")
        if sev in counts:
            counts[sev] += 1
    score_impact = sum(counts[sev] * weight for sev, weight in _SEVERITY_WEIGHTS.items())
    return {
        "_slither_metadata": True,
        "risk_impact": {
            "score_impact": round(score_impact, 2),
            "counts": counts,
            "has_critical": counts["HIGH"] > 0,
            "summary": f"{counts['HIGH']} High, {counts['MEDIUM']} Medium, {counts['LOW']} Low, {counts['INFORMATIONAL']} Info",
        },
        "total_findings": len(findings),
    }

# ──────────────────────────────────────────────────────────────
# Compilation with fallback strategies
# ──────────────────────────────────────────────────────────────

def _try_compile(main_file: Path, tmp_path: Path, remappings: list[str], allow_paths: str, debug: bool) -> Optional[Slither]:
    strategies = [
        dict(solc=_SOLC_BINARY, solc_remaps=remappings, solc_args=f"--allow-paths {allow_paths}"),
        dict(solc=_SOLC_BINARY, solc_remaps=remappings),
        dict(solc=_SOLC_BINARY, solc_args=f"--allow-paths {allow_paths}"),
        dict(solc=_SOLC_BINARY),
    ]
    for i, kwargs in enumerate(strategies, 1):
        if debug:
            print(f"[DEBUG] Compilation strategy {i}: {list(kwargs.keys())}")
        try:
            slither = Slither(str(main_file), **kwargs)
            if debug:
                print(f"[DEBUG] Strategy {i} succeeded")
            return slither
        except Exception as exc:
            if debug:
                msg = str(exc)
                lines = [l for l in msg.splitlines() if l.strip()][:3]
                print(f"[DEBUG] Strategy {i} failed: {' | '.join(lines)}")
    if debug:
        print("[DEBUG] All compilation strategies exhausted — giving up")
    return None

# ──────────────────────────────────────────────────────────────
# Core runner
# ──────────────────────────────────────────────────────────────

def run_slither_on_source(source, debug: bool = False) -> list:
    if not source:
        return []

    if not _init_binaries(debug):
        if debug:
            print("[DEBUG] Failed to initialize Slither binaries")
        return []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        written = _write_sources(source, tmp_path, debug)
        if not written:
            if debug:
                print("[DEBUG] No files written — aborting")
            return []

        if debug:
            print(f"[DEBUG] Selecting main contract from {tmp_path}")
        main_file = _find_main_sol(tmp_path, debug)
        if not main_file or not main_file.exists():
            if debug:
                print("[DEBUG] Could not identify a main .sol file")
            return []

        if debug:
            print(f"[DEBUG] Main file: {main_file.relative_to(tmp_path)}")

        remappings = _build_remappings(tmp_path, debug)
        allow_paths = _build_allow_paths(tmp_path)

        if debug:
            print(f"[DEBUG] solc wrapper: {_WRAPPER_PATH}")
            print(f"[DEBUG] solc binary: {_SOLC_BINARY}")
            print(f"[DEBUG] Allow paths: {allow_paths}")

        slither = _try_compile(main_file, tmp_path, remappings, allow_paths, debug)
        if slither is None:
            return []

        n = _register_all_detectors(slither)
        if debug:
            print(f"[DEBUG] Contracts: {len(slither.contracts)}  |  Detectors: {n}")

        try:
            raw_results = slither.run_detectors()
        except Exception as exc:
            if debug:
                print(f"[DEBUG] run_detectors() failed: {exc}")
                traceback.print_exc()
            return []

        findings = _extract_findings(raw_results, debug)
        if debug:
            print(f"[DEBUG] Total findings: {len(findings)}")

        if findings:
            findings.append(_compute_metadata(findings))

        return findings
