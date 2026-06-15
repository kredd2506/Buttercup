"""Run store — durable persistence for workflow runs (WORKFLOW_DESIGN.md §8).

A single SQLite file (steward.db) holds everything a run needs to be observable and
*resumable*: the run row, per-node status, execution logs, HITL pause/resume requests,
the serialized run context (the checkpoint), and the change record a run produced.

SQLite is the whole point here: one file, no server, built into Python, and it
survives restarts — so a run paused at a human gate is still there tomorrow.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .config import CONFIG


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    workflow      TEXT NOT NULL,
    status        TEXT NOT NULL,            -- running | waiting | ok | resumed | failed | duplicate | stopped
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    input_json    TEXT,
    output_json   TEXT,
    context_json  TEXT,                     -- the checkpoint: accumulated node outputs
    waiting_node  TEXT                       -- node id the run is paused on, if any
);
CREATE TABLE IF NOT EXISTS node_runs (
    run_id        TEXT NOT NULL,
    node_id       TEXT NOT NULL,
    kind          TEXT,
    status        TEXT NOT NULL,            -- pending | running | done | skipped | waiting | resumed | failed
    started_at    TEXT,
    finished_at   TEXT,
    PRIMARY KEY (run_id, node_id)
);
CREATE TABLE IF NOT EXISTS logs (
    run_id        TEXT NOT NULL,
    ts            TEXT NOT NULL,
    level         TEXT NOT NULL,            -- INFO | WAITING | RESUMED | SUCCESS | ERROR
    message       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hitl_requests (
    run_id        TEXT NOT NULL,
    node_id       TEXT NOT NULL,
    prompt        TEXT,
    options_json  TEXT,                     -- the "ask" payload shown to the human
    decision      TEXT,                     -- approve | modify | reject (null while waiting)
    patch_json    TEXT,                     -- the human's edits, when decision = modify
    created_at    TEXT NOT NULL,
    decided_at    TEXT,
    PRIMARY KEY (run_id, node_id)
);
CREATE TABLE IF NOT EXISTS changes (
    run_id             TEXT NOT NULL,
    conf               TEXT,
    stanza             TEXT,
    app                TEXT,
    before_text        TEXT,
    after_text         TEXT,
    events_before      INTEGER,
    events_after       INTEGER,
    ts_ok_pct          REAL,
    fields_extracted   INTEGER,
    modified_by_human  INTEGER DEFAULT 0,
    created_at         TEXT NOT NULL,
    PRIMARY KEY (run_id)
);
"""


@dataclass
class HitlRequest:
    run_id: str
    node_id: str
    prompt: str
    options: dict
    decision: Optional[str] = None
    patch: Optional[dict] = None


@dataclass
class RunRecord:
    run_id: str
    workflow: str
    status: str
    started_at: str
    finished_at: Optional[str]
    input: Any
    output: Any
    context: dict
    waiting_node: Optional[str]
    nodes: list[dict] = field(default_factory=list)
    logs: list[dict] = field(default_factory=list)
    hitl: list[dict] = field(default_factory=list)
    change: Optional[dict] = None


class RunStore:
    """Thin SQLite wrapper. One connection per call keeps it simple and thread-safe
    enough for a Streamlit/CLI app; durability comes from autocommit on close."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or CONFIG.db_path
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- runs ---------------------------------------------------------------
    def create_run(self, run_id: str, workflow: str, input_data: Any) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO runs (run_id, workflow, status, started_at, input_json, context_json) "
                "VALUES (?, ?, 'running', ?, ?, ?)",
                (run_id, workflow, _now(), json.dumps(input_data), json.dumps({})),
            )

    def save_context(self, run_id: str, context: dict) -> None:
        with self._conn() as c:
            c.execute("UPDATE runs SET context_json = ? WHERE run_id = ?",
                      (json.dumps(context, default=str), run_id))

    def set_run_status(self, run_id: str, status: str, *, waiting_node: Optional[str] = None,
                       output: Any = None, finished: bool = False) -> None:
        sets = ["status = ?"]
        vals: list[Any] = [status]
        sets.append("waiting_node = ?"); vals.append(waiting_node)
        if output is not None:
            sets.append("output_json = ?"); vals.append(json.dumps(output, default=str))
        if finished:
            sets.append("finished_at = ?"); vals.append(_now())
        vals.append(run_id)
        with self._conn() as c:
            c.execute(f"UPDATE runs SET {', '.join(sets)} WHERE run_id = ?", vals)

    # --- nodes & logs -------------------------------------------------------
    def set_node(self, run_id: str, node_id: str, kind: str, status: str) -> None:
        ts = _now()
        started = ts if status == "running" else None
        finished = ts if status in ("done", "skipped", "failed", "resumed") else None
        with self._conn() as c:
            c.execute(
                "INSERT INTO node_runs (run_id, node_id, kind, status, started_at, finished_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, node_id) DO UPDATE SET "
                "  status = excluded.status, "
                "  started_at = COALESCE(node_runs.started_at, excluded.started_at), "
                "  finished_at = COALESCE(excluded.finished_at, node_runs.finished_at)",
                (run_id, node_id, kind, status, started, finished),
            )

    def log(self, run_id: str, level: str, message: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO logs (run_id, ts, level, message) VALUES (?, ?, ?, ?)",
                      (run_id, _now(), level, message))

    # --- HITL ---------------------------------------------------------------
    def open_hitl(self, run_id: str, node_id: str, prompt: str, options: dict) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO hitl_requests (run_id, node_id, prompt, options_json, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, node_id) DO UPDATE SET "
                "  prompt = excluded.prompt, options_json = excluded.options_json, "
                "  created_at = excluded.created_at, decision = NULL, patch_json = NULL, decided_at = NULL",
                (run_id, node_id, prompt, json.dumps(options, default=str), _now()),
            )

    def record_decision(self, run_id: str, node_id: str, decision: str,
                        patch: Optional[dict] = None) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE hitl_requests SET decision = ?, patch_json = ?, decided_at = ? "
                "WHERE run_id = ? AND node_id = ?",
                (decision, json.dumps(patch) if patch else None, _now(), run_id, node_id),
            )

    # --- change record ------------------------------------------------------
    def record_change(self, run_id: str, **fields: Any) -> None:
        cols = ["run_id", "created_at", *fields.keys()]
        vals = [run_id, _now(), *fields.values()]
        placeholders = ", ".join("?" for _ in cols)
        with self._conn() as c:
            c.execute(
                f"INSERT OR REPLACE INTO changes ({', '.join(cols)}) VALUES ({placeholders})",
                vals,
            )

    # --- reads --------------------------------------------------------------
    def get_run(self, run_id: str) -> Optional[RunRecord]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if not r:
                return None
            nodes = [dict(x) for x in c.execute(
                "SELECT * FROM node_runs WHERE run_id = ? ORDER BY started_at", (run_id,))]
            logs = [dict(x) for x in c.execute(
                "SELECT * FROM logs WHERE run_id = ? ORDER BY ts", (run_id,))]
            hitl = [dict(x) for x in c.execute(
                "SELECT * FROM hitl_requests WHERE run_id = ?", (run_id,))]
            change_row = c.execute("SELECT * FROM changes WHERE run_id = ?", (run_id,)).fetchone()
        return RunRecord(
            run_id=r["run_id"], workflow=r["workflow"], status=r["status"],
            started_at=r["started_at"], finished_at=r["finished_at"],
            input=json.loads(r["input_json"]) if r["input_json"] else None,
            output=json.loads(r["output_json"]) if r["output_json"] else None,
            context=json.loads(r["context_json"]) if r["context_json"] else {},
            waiting_node=r["waiting_node"],
            nodes=nodes, logs=logs, hitl=hitl,
            change=dict(change_row) if change_row else None,
        )

    def pending_hitl(self, run_id: str) -> Optional[HitlRequest]:
        """The currently-open ask for a waiting run (decision still null)."""
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM hitl_requests WHERE run_id = ? AND decision IS NULL "
                "ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
        if not row:
            return None
        return HitlRequest(
            run_id=row["run_id"], node_id=row["node_id"], prompt=row["prompt"],
            options=json.loads(row["options_json"]) if row["options_json"] else {},
        )

    def list_runs(self, status: Optional[str] = None, limit: int = 50) -> list[dict]:
        q = "SELECT run_id, workflow, status, started_at, finished_at FROM runs"
        params: list[Any] = []
        if status:
            q += " WHERE status = ?"; params.append(status)
        q += " ORDER BY started_at DESC LIMIT ?"; params.append(limit)
        with self._conn() as c:
            rows = [dict(x) for x in c.execute(q, params)]
            # attach the headline change metric (Δ events) for the history table
            for row in rows:
                ch = c.execute(
                    "SELECT events_before, events_after FROM changes WHERE run_id = ?",
                    (row["run_id"],)).fetchone()
                row["events_before"] = ch["events_before"] if ch else None
                row["events_after"] = ch["events_after"] if ch else None
        return rows
