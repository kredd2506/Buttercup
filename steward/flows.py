"""Steward's workflows, expressed as graphs over the engine (WORKFLOW_DESIGN.md §3).

The `onboarding` flow wires the existing Steward functions (propose / preview /
verify / apply) into nodes, with two durable HITL gates: `hitl_review` (approve the
proposed parsing config) and `hitl_apply` (approve writing it after seeing the parse).

Node context is plain JSON-serializable dicts so a paused run round-trips through the
SQLite checkpoint cleanly.
"""

from __future__ import annotations

import time

from .config import CONFIG
from .runstore import RunStore
from .workflow import Engine, Node, Workflow

PREVIEW_POLL_TRIES = 6
PREVIEW_POLL_SLEEP = 5


def _as_proposal(d: dict):
    """Rebuild an OnboardingProposal from the JSON-safe dict kept in run context."""
    from .onboarding import OnboardingProposal
    return OnboardingProposal(
        sourcetype=d["sourcetype"], props=dict(d["props"]),
        rationale=d.get("rationale", ""), confidence=d.get("confidence", "low"),
        raw=d.get("raw", {}),
    )


def build_onboarding_workflow(store: RunStore) -> Workflow:
    # --- compute / read / write / llm nodes ---------------------------------
    def read_sample(ctx):
        inp = ctx["input"]
        return {"lines": len(inp["sample"].splitlines()), "source": inp.get("source")}

    def propose_config(ctx):
        from . import onboarding
        p = onboarding.propose_config(ctx["input"]["sample"])
        return {"sourcetype": p.sourcetype, "props": p.props,
                "rationale": p.rationale, "confidence": p.confidence, "raw": p.raw}

    def render_diff(ctx):
        prop = ctx["propose_config"]
        lines = [f"[{prop['sourcetype']}]"] + [f"{k} = {v}" for k, v in prop["props"].items()]
        return {"stanza_text": "\n".join(lines)}

    def preview_ingest(ctx):
        from . import onboarding
        info = onboarding.preview_ingest(ctx["input"]["source"], _as_proposal(ctx["propose_config"]))
        return info

    def verify_parse(ctx):
        from . import splunk_mcp
        spl = ctx["preview_ingest"]["verify_spl"]
        events = 0
        for _ in range(PREVIEW_POLL_TRIES):
            rows = splunk_mcp.run_query(f"search {spl} | stats count", earliest="-7d")
            events = int(rows[0]["count"]) if rows and rows[0].get("count") else 0
            if events > 0:
                break
            time.sleep(PREVIEW_POLL_SLEEP)
        return {"events": events, "verify_spl": spl}

    def apply_config(ctx):
        from . import onboarding
        return onboarding.apply_proposal(_as_proposal(ctx["propose_config"]))

    def record_change(ctx):
        prop = ctx["propose_config"]
        store.record_change(
            ctx["run_id"],
            conf="props", stanza=prop["sourcetype"], app=CONFIG.target_app,
            before_text="", after_text=ctx["render_diff"]["stanza_text"],
            events_before=0, events_after=ctx["verify_parse"]["events"],
            ts_ok_pct=None, fields_extracted=len(prop["props"]),
            modified_by_human=1 if prop.get("_modified_by_human") else 0,
        )
        return {"recorded": True, "events_after": ctx["verify_parse"]["events"]}

    # --- HITL nodes ---------------------------------------------------------
    def ask_review(ctx):
        prop = ctx["propose_config"]
        return {
            "prompt": f"Review proposed parsing for [{prop['sourcetype']}] "
                      f"(confidence: {prop['confidence']})",
            "options": {"stanza": ctx["render_diff"]["stanza_text"],
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
        v = ctx["verify_parse"]
        return {
            "prompt": f"Preview parsed {v['events']} event(s). Apply this config to "
                      f"app '{CONFIG.target_app}'?",
            "options": {"events": v["events"], "actions": ["approve", "reject"]},
        }

    nodes = [
        Node("read_sample", "R", run=read_sample),
        Node("propose_config", "L", run=propose_config),
        Node("render_diff", "C", run=render_diff),
        Node("hitl_review", "H", ask=ask_review, apply_patch=patch_review),
        Node("preview_ingest", "W", run=preview_ingest),
        Node("verify_parse", "R", run=verify_parse),
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
