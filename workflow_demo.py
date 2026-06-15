#!/usr/bin/env python3
"""Headless proof of the workflow engine + durable HITL pause/resume.

Drives the onboarding graph through both human gates without any UI:

    start → WAITING (hitl_review) → resume(modify) → WAITING (hitl_apply)
          → resume(approve) → OK

Run:  source .venv/bin/activate && python workflow_demo.py
"""

from pathlib import Path

from steward.flows import default_engine
from steward.history import format_change_card, format_history
from steward.runstore import RunStore


def show(run, label):
    print(f"\n=== {label} ===")
    print(f"run {run.run_id}  status={run.status.upper()}  waiting_node={run.waiting_node}")
    print("nodes: " + ", ".join(f"{n['node_id']}:{n['status']}" for n in run.nodes))


def main():
    # Isolated demo DB so we don't touch the app's steward.db.
    store = RunStore("/tmp/steward_demo.db")
    engine = default_engine(store)

    sample_path = "datasets/messy_app.log"
    sample = Path(sample_path).read_text()

    # 1) Start — engine runs read → propose → render, then pauses at hitl_review.
    run = engine.start("onboarding", {"sample": sample, "source": sample_path})
    show(run, "1. STARTED → paused at first gate")
    ask = store.pending_hitl(run.run_id)
    print("ask:", ask.prompt)
    assert run.status == "waiting" and run.waiting_node == "hitl_review"

    # 2) Resume with a MODIFY — human flips SHOULD_LINEMERGE off (the classic fix).
    run = engine.resume(run.run_id, {"action": "modify",
                                     "patch": {"props": {"SHOULD_LINEMERGE": "false"}}})
    show(run, "2. RESUMED (modify) → ingest+verify ran → paused at second gate")
    ask = store.pending_hitl(run.run_id)
    print("ask:", ask.prompt)
    assert run.status == "waiting" and run.waiting_node == "hitl_apply"

    # 3) Resume with APPROVE — config is applied, change recorded, run completes.
    run = engine.resume(run.run_id, {"action": "approve"})
    show(run, "3. RESUMED (approve) → applied → DONE")
    assert run.status == "ok", f"expected ok, got {run.status}"

    # --- the observability payload the UI will render --------------------------
    print("\n=== EXECUTION LOG ===")
    for entry in run.logs:
        print(f"  {entry['ts'][11:19]} [{entry['level']:<7}] {entry['message']}")

    print("\n=== CHANGE RECORD (what changed & improved) ===")
    print(format_change_card(run))

    print("\n=== RUN HISTORY (list_runs) ===")
    print(format_history(store.list_runs()))

    print("\n✅ HITL pause/resume cycle proven end-to-end.")


if __name__ == "__main__":
    main()
