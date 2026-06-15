"""Health diagnostics — the checks a senior admin would run, explained plainly.

Each check runs read-only SPL through the MCP Server, then Groq turns the raw
numbers into a plain-language finding plus a suggested fix. Fixes are proposals
only — nothing is applied here.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import splunk_mcp
from .llm_groq import complete_json

# (label, SPL). Tuned for a fresh single-instance dev deployment.
_CHECKS: list[tuple[str, str]] = [
    (
        "Skipped scheduled searches",
        "index=_internal sourcetype=scheduler status=skipped "
        "| stats count by savedsearch_name | sort -count | head 10",
    ),
    (
        "License usage by sourcetype",
        "index=_internal source=*license_usage.log type=Usage "
        "| stats sum(b) as bytes by st | eval MB=round(bytes/1024/1024,2) "
        "| sort -MB | head 10",
    ),
    (
        "Indexing errors",
        "index=_internal log_level=ERROR component=* "
        "| stats count by component | sort -count | head 10",
    ),
]

_SYSTEM = """You are a senior Splunk administrator reviewing one diagnostic result.
Given a check name and its raw search rows, return a JSON object with:
- "severity": "ok" | "info" | "warning" | "critical"
- "finding": one plain-language sentence a non-expert can understand
- "suggested_fix": one concrete next step, or "" if nothing is needed
Be honest: if the rows are empty, say so and use severity "ok"."""


@dataclass
class Finding:
    check: str
    severity: str
    finding: str
    suggested_fix: str
    rows: list[dict]


def run_health_check(earliest: str = "-24h") -> list[Finding]:
    findings: list[Finding] = []
    for label, spl in _CHECKS:
        try:
            rows = splunk_mcp.run_query(spl, earliest=earliest)
        except Exception as exc:  # surface connection issues as a finding
            findings.append(
                Finding(label, "warning", f"Check could not run: {exc}", "", [])
            )
            continue

        verdict = complete_json(
            _SYSTEM, f"Check: {label}\nRows (JSON): {rows[:20]}"
        )
        findings.append(
            Finding(
                check=label,
                severity=verdict.get("severity", "info"),
                finding=verdict.get("finding", ""),
                suggested_fix=verdict.get("suggested_fix", ""),
                rows=rows,
            )
        )
    return findings
