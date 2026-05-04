# analysis/correlate.py
"""
Correlation engine for multi‑tool findings.
Groups issues by vulnerability class and location to identify consensus.
"""

def _normalize_title(title: str) -> str:
    """Strip common prefixes/suffixes for better matching."""
    title = title.lower().replace("_", " ").replace("-", " ")
    # Remove tool-specific prefixes
    for prefix in ["goplus: ", "mythril: ", "slither: "]:
        if title.startswith(prefix):
            title = title[len(prefix):]
    return title.strip()


def _extract_line(finding: dict) -> int | None:
    """Get line number if present."""
    line = finding.get("line") or finding.get("code_ref")
    if isinstance(line, str):
        try:
            return int(line.split(":")[-1]) if ":" in line else int(line)
        except (ValueError, TypeError):
            pass
    return line


def correlate_findings(
    slither_findings: list,
    mythril_findings: list,
    goplus_findings: list,
    static_findings: list,
    ai_findings: list = None
) -> dict:
    """
    Group findings across all tools and identify consensus.
    
    Returns:
        {
            "consensus": [finding_dict, ...],  # detected by >=2 tools
            "single": [finding_dict, ...],     # detected by exactly 1 tool
            "total_tools": int,
            "by_severity": {"CRITICAL": int, "HIGH": int, ...}
        }
    """
    all_findings = []
    
    # Tag each finding with its source
    for f in slither_findings:
        f = f.copy()
        f["_source"] = "slither"
        all_findings.append(f)
    
    for f in mythril_findings:
        f = f.copy()
        f["_source"] = "mythril"
        all_findings.append(f)
    
    for f in goplus_findings:
        f = f.copy()
        f["_source"] = "goplus"
        all_findings.append(f)
    
    for f in static_findings:
        f = f.copy()
        f["_source"] = "static"
        all_findings.append(f)
    
    if ai_findings:
        for f in ai_findings:
            f = f.copy()
            f["_source"] = "ai"
            all_findings.append(f)
    
    # Group by (normalized_title, line)
    groups = {}
    for f in all_findings:
        norm_title = _normalize_title(f.get("title", "") or f.get("detector", ""))
        line = _extract_line(f)
        key = (norm_title, line)
        if key not in groups:
            groups[key] = []
        groups[key].append(f)
    
    consensus = []
    single = []
    severity_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "INFO": 0}
    
    for key, findings in groups.items():
        # Merge findings: use the most detailed description and highest severity
        sources = list({f["_source"] for f in findings})
        best = max(findings, key=lambda x: (
            {"CRITICAL": 5, "HIGH": 4, "MEDIUM": 3, "LOW": 2, "INFO": 1}.get(x.get("severity", "INFO"), 0)
        ))
        
        merged = {
            "title": best.get("title") or best.get("detector", "Unknown"),
            "severity": best.get("severity", "INFO"),
            "description": best.get("description", ""),
            "line": _extract_line(best),
            "sources": sources,
            "source_count": len(sources),
            "consensus": len(sources) >= 2,
        }
        # Preserve any tool-specific fields
        if "swc_id" in best:
            merged["swc_id"] = best["swc_id"]
        if "code_ref" in best:
            merged["code_ref"] = best["code_ref"]
        
        if merged["consensus"]:
            consensus.append(merged)
        else:
            single.append(merged)
        
        severity_counts[merged["severity"]] = severity_counts.get(merged["severity"], 0) + 1
    
    # Sort consensus by severity, then by source count
    severity_order = {"CRITICAL": 5, "HIGH": 4, "MEDIUM": 3, "LOW": 2, "INFO": 1}
    consensus.sort(key=lambda x: (-severity_order.get(x["severity"], 0), -x["source_count"]))
    single.sort(key=lambda x: -severity_order.get(x["severity"], 0))
    
    return {
        "consensus": consensus,
        "single": single,
        "total_tools": len(set(f["_source"] for f in all_findings)),
        "by_severity": severity_counts,
    }