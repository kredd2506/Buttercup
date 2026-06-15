"""History & change presentation helpers (WORKFLOW_DESIGN.md §4–5).

Pure formatting over RunRecords and run-history rows from the store. UI-agnostic:
the Streamlit/React/CLI layers all render the same structures produced here.
"""

from __future__ import annotations

import difflib
import json
from typing import Optional

from .runstore import RunRecord


def provenance(run: RunRecord) -> list[dict]:
    """Field-level human edits (LLM value → human value) recorded during a modify."""
    ch = run.change
    if not ch or not ch.get("provenance_json"):
        return []
    try:
        return json.loads(ch["provenance_json"])
    except (ValueError, TypeError):
        return []


def change_diff(run: RunRecord) -> str:
    """Unified diff of the config a run changed (before → after). '' if no change record."""
    ch = run.change
    if not ch:
        return ""
    before = (ch.get("before_text") or "").splitlines()
    after = (ch.get("after_text") or "").splitlines()
    return "\n".join(difflib.unified_diff(
        before, after, fromfile="before", tofile="after", lineterm=""))


def improvement(run: RunRecord) -> Optional[dict]:
    """The headline 'what improved' numbers for a run, or None if it didn't change anything."""
    ch = run.change
    if not ch:
        return None
    before, after = ch.get("events_before"), ch.get("events_after")
    pct = None
    if before is not None and after is not None and before > 0:
        pct = round(100.0 * (after - before) / before, 1)
    return {
        "events_before": before,
        "events_after": after,
        "events_delta_pct": pct,
        "ts_ok_pct": ch.get("ts_ok_pct"),
        "fields_extracted": ch.get("fields_extracted"),
        "modified_by_human": bool(ch.get("modified_by_human")),
    }


def format_history(rows: list[dict]) -> str:
    """Render run-history rows as the screenshot-style table (created_at | status | …)."""
    head = f"{'created_at':<20} {'status':<9} {'workflow':<11} {'Δ events':<12} run_id"
    lines = [head, "-" * len(head)]
    for r in rows:
        eb, ea = r.get("events_before"), r.get("events_after")
        delta = f"{eb} → {ea}" if ea is not None else "—"
        lines.append(
            f"{r['started_at']:<20} {r['status']:<9} {r['workflow']:<11} {delta:<12} {r['run_id']}")
    return "\n".join(lines)


def format_change_card(run: RunRecord) -> str:
    """A compact 'what changed & improved' card for one run (CLI/Markdown friendly)."""
    ch = run.change
    if not ch:
        return f"run {run.run_id}: no change recorded (status={run.status})"
    imp = improvement(run)
    pct = f" ({imp['events_delta_pct']:+}%)" if imp and imp["events_delta_pct"] is not None else ""
    out = [
        f"Change — run {run.run_id} · {run.workflow}",
        f"  props.conf [{ch['stanza']}] in app '{ch['app']}'",
        "",
        change_diff(run),
        "",
        f"  events parsed     {ch['events_before']} → {ch['events_after']}{pct}",
        f"  timestamp-ok      {ch['ts_ok_pct']}%",
        f"  fields extracted  {ch['fields_extracted']}",
        f"  modified by human {bool(ch['modified_by_human'])}",
    ]
    prov = provenance(run)
    if prov:
        out.append("  human edits (LLM → human):")
        out += [f"    {p['field']}: {p['llm']!r} → {p['human']!r}" for p in prov]
    return "\n".join(out)
