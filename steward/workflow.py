"""Workflow engine — resumable graph runs with durable HITL pauses.

See WORKFLOW_DESIGN.md. A Workflow is an ordered graph of Nodes. The engine drives
nodes in order, recording status + logs for each. When it reaches a HITL `[H]` node
it does NOT block: it writes a checkpoint (the run context) to the store, opens an
"ask" for a human, marks the run `waiting`, and returns. Later, `resume(run_id,
decision)` reloads the checkpoint and continues — so a paused run survives a refresh
or a process restart, and any surface (UI, CLI, a future webhook) can resume it.

`start()` and `resume()` share one loop (`_drive`): pop the next node, run it, persist;
if it's HITL, pause. That single loop is what makes pause/resume symmetric.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .runstore import RunRecord, RunStore

# Decision actions a human can return at a HITL node.
APPROVE, MODIFY, REJECT = "approve", "modify", "reject"


@dataclass
class Node:
    """One workflow step.

    Non-HITL nodes set `run(ctx) -> output`; the output is stored in ctx[id].
    HITL nodes set `ask(ctx) -> {"prompt", "options"}` and (optionally) `apply_patch`
    to merge a human's edits into the context on a `modify` decision.
    """
    id: str
    kind: str                                            # R | L | C | H | W
    run: Optional[Callable[[dict], Any]] = None
    ask: Optional[Callable[[dict], dict]] = None
    apply_patch: Optional[Callable[[dict, dict], None]] = None

    @property
    def is_hitl(self) -> bool:
        return self.kind == "H"


@dataclass
class Workflow:
    name: str
    nodes: list[Node]

    def index(self, node_id: str) -> int:
        for i, n in enumerate(self.nodes):
            if n.id == node_id:
                return i
        raise KeyError(f"no node {node_id!r} in workflow {self.name!r}")


class Engine:
    def __init__(self, store: Optional[RunStore] = None):
        self.store = store or RunStore()
        self._registry: dict[str, Workflow] = {}

    def register(self, workflow: Workflow) -> None:
        self._registry[workflow.name] = workflow

    # --- entry points -------------------------------------------------------
    def start(self, workflow_name: str, input_data: Any, run_id: Optional[str] = None) -> RunRecord:
        wf = self._registry[workflow_name]
        run_id = run_id or uuid.uuid4().hex[:12]
        self.store.create_run(run_id, workflow_name, input_data)
        self.store.log(run_id, "INFO", f"start {workflow_name}")
        ctx: dict = {"input": input_data, "run_id": run_id}
        return self._drive(run_id, wf, ctx, start_index=0)

    def resume(self, run_id: str, decision: dict) -> RunRecord:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(f"unknown run {run_id!r}")
        if run.status != "waiting":
            raise ValueError(f"run {run_id} is not waiting (status={run.status})")

        wf = self._registry[run.workflow]
        node = wf.nodes[wf.index(run.waiting_node)]
        ctx = run.context
        action = decision.get("action", APPROVE)
        patch = decision.get("patch")
        self.store.record_decision(run_id, node.id, action, patch)

        if action == REJECT:
            self.store.set_node(run_id, node.id, "H", "skipped")
            self.store.log(run_id, "INFO", f"{node.id} — rejected, stopping")
            self.store.set_run_status(run_id, "stopped", finished=True)
            return self.store.get_run(run_id)

        if action == MODIFY and patch:
            if node.apply_patch:
                node.apply_patch(ctx, patch)
            self.store.log(run_id, "RESUMED", f"{node.id} — modify {patch}")
        else:
            self.store.log(run_id, "RESUMED", f"{node.id} — approved")

        self.store.set_node(run_id, node.id, "H", "resumed")
        self.store.save_context(run_id, ctx)
        self.store.set_run_status(run_id, "resumed")
        # Continue forward with the (possibly patched) context.
        return self._drive(run_id, wf, ctx, start_index=wf.index(node.id) + 1)

    # --- the shared loop ----------------------------------------------------
    def _drive(self, run_id: str, wf: Workflow, ctx: dict, start_index: int) -> RunRecord:
        for i in range(start_index, len(wf.nodes)):
            node = wf.nodes[i]

            if node.is_hitl:
                ask = node.ask(ctx) if node.ask else {"prompt": node.id, "options": {}}
                self.store.set_node(run_id, node.id, "H", "waiting")
                self.store.open_hitl(run_id, node.id, ask.get("prompt", node.id),
                                     ask.get("options", {}))
                self.store.save_context(run_id, ctx)
                self.store.set_run_status(run_id, "waiting", waiting_node=node.id)
                self.store.log(run_id, "WAITING", f"{node.id} — awaiting human")
                return self.store.get_run(run_id)

            self.store.set_node(run_id, node.id, node.kind, "running")
            self.store.log(run_id, "INFO", node.id)
            try:
                out = node.run(ctx) if node.run else None
            except Exception as exc:  # a node error fails the whole run, observably
                self.store.set_node(run_id, node.id, node.kind, "failed")
                self.store.log(run_id, "ERROR", f"{node.id}: {exc}")
                self.store.set_run_status(run_id, "failed", finished=True)
                return self.store.get_run(run_id)

            ctx[node.id] = out
            self.store.set_node(run_id, node.id, node.kind, "done")
            self.store.save_context(run_id, ctx)

        # Reached the end — success.
        self.store.set_run_status(run_id, "ok", output=ctx.get("output", ctx), finished=True)
        self.store.log(run_id, "SUCCESS", f"completed {wf.name}")
        return self.store.get_run(run_id)
