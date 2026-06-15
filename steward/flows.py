"""Steward's workflows, expressed as graphs over the engine (WORKFLOW_DESIGN.md §3).

The `onboarding` flow wires the existing Steward functions (propose / preview /
verify / apply) into nodes, with two durable HITL gates: `hitl_review` (approve the
proposed parsing config) and `hitl_apply` (approve writing it after seeing the parse).

Step 2 adds honest change records: it captures the real "before" config, ingests a
baseline (Splunk's default parsing) alongside the proposed config, and measures the
improvement (events parsed + timestamp-correctness) before anything is applied.

Node context is plain JSON-serializable dicts so a paused run round-trips through the
SQLite checkpoint cleanly.
"""

from __future__ import annotations

import time

from .config import CONFIG
from .runstore import RunStore
from .workflow import Engine, Node, Workflow

PREVIEW_INDEX = "steward_preview"
POLL_TRIES = 6
POLL_SLEEP = 5


def _as_proposal(d: dict):
    """Rebuild an OnboardingProposal from the JSON-safe dict kept in run context."""
    from .onboarding import OnboardingProposal
    return OnboardingProposal(
        sourcetype=d["sourcetype"], props=dict(d["props"]),
        rationale=d.get("rationale", ""), confidence=d.get("confidence", "low"),
        raw=d.get("raw", {}),
    )


def _count_events(sourcetype: str) -> int:
    """Poll the preview index until events for a sourcetype show up (async indexing)."""
    from . import splunk_mcp
    for _ in range(POLL_TRIES):
        rows = splunk_mcp.run_query(
            f"search index={PREVIEW_INDEX} sourcetype={sourcetype} | stats count",
            earliest="-7d")
        c = int(rows[0]["count"]) if rows and rows[0].get("count") else 0
        if c > 0:
            return c
        time.sleep(POLL_SLEEP)
    return 0


def _timestamp_ok_pct(sourcetype: str):
    """% of events whose _time was genuinely parsed from the event (not index-time fallback).

    When timestamp extraction fails, Splunk assigns _time ~= _indextime. So a gap of
    more than a minute between them means the timestamp was really extracted.
    """
    from . import splunk_mcp
    rows = splunk_mcp.run_query(
        f"search index={PREVIEW_INDEX} sourcetype={sourcetype} "
        f"| eval ok=if((_indextime - _time) > 60, 1, 0) | stats sum(ok) as ok, count as total",
        earliest="-7d")
    if not rows:
        return None
    total = int(float(rows[0].get("total") or 0))
    ok = int(float(rows[0].get("ok") or 0))
    return round(100.0 * ok / total, 1) if total else None


def build_onboarding_workflow(store: RunStore) -> Workflow:
    # --- read / llm / compute ----------------------------------------------
    def read_sample(ctx):
        inp = ctx["input"]
        return {"lines": len(inp["sample"].splitlines()), "source": inp.get("source")}

    def propose_config(ctx):
        from . import onboarding
        p = onboarding.propose_config(ctx["input"]["sample"])
        return {"sourcetype": p.sourcetype, "props": p.props,
                "rationale": p.rationale, "confidence": p.confidence, "raw": p.raw}

    def read_current(ctx):
        """Capture the real 'before' — existing props.conf settings for the keys we'd change."""
        from . import splunk_rest
        prop = ctx["propose_config"]
        existing = splunk_rest.read_conf_stanza("props", prop["sourcetype"])
        before = {k: existing[k] for k in prop["props"] if k in existing}
        if before:
            before_text = f"[{prop['sourcetype']}]\n" + "\n".join(f"{k} = {v}" for k, v in before.items())
        else:
            before_text = f"# no existing [{prop['sourcetype']}] stanza — Splunk default parsing"
        return {"before": before, "before_text": before_text}

    def render_diff(ctx):
        prop = ctx["propose_config"]
        lines = [f"[{prop['sourcetype']}]"] + [f"{k} = {v}" for k, v in prop["props"].items()]
        return {"stanza_text": "\n".join(lines)}

    # --- write / measure ----------------------------------------------------
    def preview_ingest(ctx):
        from . import onboarding
        return onboarding.preview_ingest(ctx["input"]["source"], _as_proposal(ctx["propose_config"]))

    def baseline_probe(ctx):
        """Ingest the same sample with NO custom props (Splunk defaults) for an honest baseline."""
        from . import container, splunk_rest
        base_st = ctx["propose_config"]["sourcetype"] + ":baseline"
        path = container.stage_file(ctx["input"]["source"])
        splunk_rest.ensure_index(PREVIEW_INDEX)
        splunk_rest.oneshot_ingest(path, PREVIEW_INDEX, base_st)
        return {"baseline_sourcetype": base_st}

    def measure(ctx):
        proposed = ctx["propose_config"]["sourcetype"]
        baseline = ctx["baseline_probe"]["baseline_sourcetype"]
        events_after = _count_events(proposed)
        events_before = _count_events(baseline)
        return {"events_before": events_before, "events_after": events_after,
                "ts_ok_pct": _timestamp_ok_pct(proposed)}

    def apply_config(ctx):
        from . import onboarding
        return onboarding.apply_proposal(_as_proposal(ctx["propose_config"]))

    def record_change(ctx):
        prop = ctx["propose_config"]
        m = ctx["measure"]
        store.record_change(
            ctx["run_id"],
            conf="props", stanza=prop["sourcetype"], app=CONFIG.target_app,
            before_text=ctx["read_current"]["before_text"], after_text=ctx["render_diff"]["stanza_text"],
            events_before=m["events_before"], events_after=m["events_after"],
            ts_ok_pct=m["ts_ok_pct"], fields_extracted=len(prop["props"]),
            modified_by_human=1 if prop.get("_modified_by_human") else 0,
        )
        return {"recorded": True, "events": [m["events_before"], m["events_after"]]}

    # --- HITL gates ---------------------------------------------------------
    def ask_review(ctx):
        prop = ctx["propose_config"]
        return {
            "prompt": f"Review proposed parsing for [{prop['sourcetype']}] "
                      f"(confidence: {prop['confidence']})",
            "options": {"stanza": ctx["render_diff"]["stanza_text"],
                        "before": ctx["read_current"]["before_text"],
                        "actions": ["approve", "modify", "reject"]},
        }

    def patch_review(ctx, patch):
        """Merge a human's edits into the proposal, and flag the run as modified."""
        prop = ctx["propose_config"]
        if "sourcetype" in patch:
            prop["sourcetype"] = patch["sourcetype"]
        if "props" in patch:
            prop["props"].update(patch["props"])
        prop["_modified_by_human"] = True

    def ask_apply(ctx):
        m = ctx["measure"]
        return {
            "prompt": f"Baseline parsed {m['events_before']} event(s); proposed config parses "
                      f"{m['events_after']} (timestamp-ok {m['ts_ok_pct']}%). Apply to app "
                      f"'{CONFIG.target_app}'?",
            "options": {"events_before": m["events_before"], "events_after": m["events_after"],
                        "ts_ok_pct": m["ts_ok_pct"], "actions": ["approve", "reject"]},
        }

    nodes = [
        Node("read_sample", "R", run=read_sample),
        Node("propose_config", "L", run=propose_config),
        Node("read_current", "R", run=read_current),
        Node("render_diff", "C", run=render_diff),
        Node("hitl_review", "H", ask=ask_review, apply_patch=patch_review),
        Node("preview_ingest", "W", run=preview_ingest),
        Node("baseline_probe", "W", run=baseline_probe),
        Node("measure", "R", run=measure),
        Node("hitl_apply", "H", ask=ask_apply),
        Node("apply_config", "W", run=apply_config),
        Node("record_change", "C", run=record_change),
    ]
    return Workflow("onboarding", nodes)


def default_engine(store: RunStore | None = None) -> Engine:
    """An engine with Steward's workflows registered, sharing one run store."""
    store = store or RunStore()
    engine = Engine(store)
    engine.register(build_onboarding_workflow(store))
    return engine
