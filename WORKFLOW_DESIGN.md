# Splunk Steward — Workflow & Observability Design

> **Status:** design / functionality-first. UI is deliberately deferred — everything
> below is the *behavior* and *data*. Once this is agreed, any UI (Streamlit panels
> now, a React canvas later) is just a view over the same engine.

This design turns Steward's current linear flows into an explicit, observable,
**resumable workflow graph** — inspired by the reference bookkeeping agent:

- a **node-graph** of steps, each with its own status (the canvas),
- a **run history** showing every execution and *what changed & improved* (the table),
- a **human-in-the-loop (HITL) node** that pauses the run, waits for a person, and resumes.

It directly answers the three needs:
1. **Logs & runs — what changed/improved** → §4 Run history + §5 Change records
2. **Human-in-the-loop setup** → §6 HITL pause/resume node *(priority)*
3. **Modification after changing** → §6.3 Modify-and-resume

---

## 1. Core concepts

| Concept | Plain meaning |
|---|---|
| **Workflow** | A named graph (DAG) of steps. Steward has `onboarding` and `health_check`. |
| **Node** | One step. Has a *kind*: `[R]` read, `[L]` LLM, `[C]` compute, `[H]` HITL, `[W]` write. |
| **Edge** | A labeled arrow from one node to the next ("approved", "ingested", "modify"). |
| **Run** | One execution of a workflow. Records status, per-node state, input, output, logs. |
| **Checkpoint** | A saved snapshot of a paused run, so it can resume later (even after a restart). |
| **Change record** | The before/after diff + improvement metrics a run produced. |

### Node status lifecycle
```
pending ──▶ running ──▶ done
                │  └────▶ skipped        (branch not taken)
                │  └────▶ failed         (error; run fails)
                └───────▶ waiting        (HITL node — awaiting a human)  ──▶ resumed ──▶ done
```

### Run status (mirrors the reference: ok / resumed / waiting / duplicate / failed)
```
running ──▶ waiting ──(human decides)──▶ resumed ──▶ ok
   │           │                            │
   │           └── reject ──▶ stopped       └── (more HITL nodes loop through waiting again)
   │
   ├── duplicate   (this exact change was already applied — short-circuit, nothing written)
   └── failed      (a node errored)
```

---

## 2. Why a graph (not just functions)

The current code already *is* a pipeline — `propose_config → preview_ingest →
apply_proposal`. Making it an explicit graph buys three things the screenshots show:

- **Observability** — every node's status and timing is recorded, so a run is
  inspectable ("Pass 2 — Done", "HITL reply — Waiting"), not a black box.
- **Resumability** — a run can *stop* at a human gate and *continue* later from the
  exact same place. This is what makes HITL real instead of a blocking `st.button`.
- **Auditability** — each run is a row in history with a diff, so you can see what
  Steward changed and whether it improved things.

---

## 3. Steward's workflows as graphs

### 3.1 `onboarding` (the centerpiece)
Maps almost 1:1 onto the reference's "Parse Bill" graph, with two HITL gates.

```
            ┌───────────────┐
  sample ──▶│ read_sample   │ [R]  load the raw log sample
            └──────┬────────┘
                   │ raw text
            ┌──────▼────────┐
            │ propose_config│ [L]  Groq infers props.conf   (≈ "Pass 1")
            └──────┬────────┘
                   │ proposal {sourcetype, props, rationale, confidence}
            ┌──────▼────────┐
            │ render_diff   │ [C]  build the props.conf stanza + confidence
            └──────┬────────┘
                   │ diff
        ┌──────────▼──────────────┐   modify (edit the proposal)
   ┌───▶│   hitl_review      [H]  │──────────────────────┐
   │    │   Approve / Modify / Reject  ── status: WAITING │
   │    └──────────┬──────────────┘                       │
   │   reject      │ approved                             │
   │  (stopped)    ▼                                       │
   │        ┌───────────────┐                              │
   │        │ preview_ingest│ [W]  stage via docker cp →   │
   │        └──────┬────────┘      oneshot into steward_preview
   │               │ ingested                              │
   │        ┌──────▼────────┐                              │
   │        │ verify_parse  │ [R]  run verify_spl via MCP, │
   │        └──────┬────────┘      measure parse quality    │
   │               │ quality {events, ts_ok%, fields}      │
   │        ┌──────▼──────────────┐                        │
   │        │   hitl_apply    [H] │  status: WAITING       │
   │        │   Approve / Reject  │                        │
   │        └──────┬──────────────┘                        │
   │               │ approved                              │
   │        ┌──────▼────────┐                              │
   │        │ apply_config  │ [W]  write props.conf to     │
   │        └──────┬────────┘      STEWARD_TARGET_APP       │
   │        ┌──────▼────────┐                              │
   │        │ record_change │ [C]  persist diff + Δmetrics │
   │        └───────────────┘                              │
   └──────────────── modify loops back to propose ─────────┘
```

### 3.2 `health_check`
```
 ┌─────────────┐   ┌──────────────┐   ┌───────────────┐   ┌──────────────┐
 │ run_checks  │──▶│ summarize    │──▶│ compare_prev  │──▶│ record_run   │
 │ [R] SPL×N   │   │ [L] plain-lang│   │ [C] vs last run│   │ [C] snapshot │
 └─────────────┘   └──────────────┘   └───────────────┘   └──────────────┘
```
No HITL — health is read-only. `compare_prev` is what gives you *"what improved"*
across runs (e.g. skipped searches **12 → 3** since yesterday).

---

## 4. Run history (the "logs & runs" view)

Every execution is one row. This is the screenshot-1 table, mapped to Steward:

```
created_at           status      workflow     sourcetype            Δ events   view
─────────────────────────────────────────────────────────────────────────────────
2026-06-15 02:31:04  ok          onboarding   app:log:multiformat   1 → 14     view
2026-06-15 02:19:03  waiting     onboarding   app:log:multiformat   —          view   ◀ paused at hitl_review
2026-06-15 01:50:11  resumed→ok  onboarding   app:log:multiformat   1 → 14     view   ◀ human modified
2026-06-15 01:40:22  duplicate   onboarding   app:log:multiformat   —          view   ◀ same change already applied
2026-06-15 01:12:46  failed      health       —                     —          view
```

"Δ events" is one chosen headline metric; the full change record (§5) lives in `view`.

---

## 5. Change records — *what changed & improved*

Each run that touches Splunk writes a change record. This is the heart of need #1 —
not just "a config was applied" but **the diff and whether it actually helped.**

```
┌ Change — run 7f3a · onboarding ───────────────────────────────────────┐
│ file: props.conf   stanza: [app:log:multiformat]   app: search        │
│                                                                        │
│   - SHOULD_LINEMERGE = true        ◀ LLM v1 (merged all lines → 1 event)│
│   + SHOULD_LINEMERGE = false       ◀ human-approved correction         │
│   + LINE_BREAKER = (\r\n|\n)(?=\d{2}/\w{3}/\d{4}|\d{4}-\d{2}-\d{2}|...) │
│     TIME_FORMAT = %d/%b/%Y %H:%M:%S.%3N        (unchanged)             │
│                                                                        │
│   Improvement                                                          │
│     events parsed      1  →  14     (+1300%)                           │
│     timestamp-ok       12% →  93%                                      │
│     fields extracted    3 →   7                                        │
│                                                                        │
│   Provenance:  proposed by Groq · MODIFIED by human at hitl_review     │
└────────────────────────────────────────────────────────────────────────┘
```

**Improvement metrics** (computed by `verify_parse` against `steward_preview`):
- `events_parsed` — event count (catches the merge bug: 1 vs 14).
- `timestamp_ok_pct` — % of events whose `_time` was parsed (not index-time fallback).
- `fields_extracted` — distinct extracted fields.
- For `health_check`: each metric is compared to the **previous run** of the same check.

---

## 6. HITL pause/resume node  *(priority build)*

The reference's "HITL reply" node is the model: the run reaches a node, **stops**,
shows a human a decision, and only continues once they respond. Today Steward fakes
this with a Streamlit button that blocks the script. We make it a **real, durable
pause** so the run survives a refresh/restart and can be resumed by any caller.

### 6.1 The state machine
```
   ...running...
        │
        ▼
 ┌─────────────────┐   engine hits a [H] node
 │  hitl_review    │   • node.status   = waiting
 │  status=WAITING │   • run.status    = waiting
 └────────┬────────┘   • write a checkpoint + a hitl_request (the "ask")
          │            • engine RETURNS (does not block; process may even exit)
          │
   (time passes — could be seconds or a day)
          │
          ▼
 ┌─────────────────┐   a human submits a decision:
 │   decision       │     approve              → continue unchanged
 │  approve/modify/ │     modify { patch }     → patch proposal, mark "modified"
 │   reject         │     reject               → run.status = stopped
 └────────┬────────┘
          │ approve | modify
          ▼
 ┌─────────────────┐   engine.resume(run_id, decision):
 │   resumed        │   • load checkpoint, apply decision to ctx
 │   continue DAG   │   • node.status = done, run.status = resumed
 └─────────────────┘   • run forward to the next node (or next HITL pause)
```

### 6.2 Sequence — onboarding with the gate
```
UI/CLI            Engine                         Store                 Splunk/Groq
  │  start(onboarding, sample)                     │                        │
  │ ───────────────▶ propose_config ───────────────────────────────────────▶ Groq
  │                  render_diff                    │                        │
  │                  hitl_review → WAITING ─────────▶ checkpoint + ask        │
  │ ◀── run{status:waiting, ask:{proposal,opts}} ───│                        │
  │  (human reads the proposed props.conf)          │                        │
  │  resume(run_id, {approve})                       │                        │
  │ ───────────────▶ load checkpoint ◀──────────────│                        │
  │                  preview_ingest ────────────────────────────────────────▶ docker cp + oneshot
  │                  verify_parse ──────────────────────────────────────────▶ MCP (Δ events)
  │                  hitl_apply → WAITING ───────────▶ checkpoint + ask        │
  │ ◀── run{status:waiting, ask:{quality,opts}} ────│                        │
  │  resume(run_id, {approve})                       │                        │
  │ ───────────────▶ apply_config ──────────────────────────────────────────▶ REST props.conf
  │                  record_change ─────────────────▶ change record           │
  │ ◀── run{status:ok, change:{1→14}} ──────────────│                        │
```

### 6.3 Modification (need #3)
At a HITL node the human can **edit the proposal before it proceeds**:
```
 decision = {
   action: "modify",
   patch:  { props: { "SHOULD_LINEMERGE": "false" } }   # only the keys they changed
 }
```
On resume the engine merges `patch` into the proposal in the run context, records a
**`modified_by_human = true`** flag plus a field-level diff (LLM value → human value),
and continues with the human's version. That diff becomes part of the change record's
"Provenance" line — so history shows *the human flipped SHOULD_LINEMERGE*, which is
exactly the demo story.

### 6.4 Why durable (not a blocking button)
Because the pause is a persisted checkpoint, not a function sitting on the stack:
- the run survives a Streamlit rerun, a page refresh, or a process restart;
- a *different* surface can resume it (UI today, an email-reply webhook later — just
  like the reference resumes from a Gmail reply);
- two HITL gates in one workflow "just work" — each is another pause/resume cycle.

---

## 7. Run detail (input / output / logs)

Mirrors the screenshot-2 right panel. All of it comes straight from the run record:
```
┌ output ──────────────────────────┐ ┌ input ───────────────────────────┐
│ { "applied":"app:log:multiformat",│ │ { "sample":"[14/Jun/2026 09:12…]",│
│   "events":14, "ts_ok":0.93,      │ │   "lines":25, "source":"datasets/ │
│   "modified_by_human":true }      │ │    messy_app.log" }               │
└──────────────────────────────────┘ └───────────────────────────────────┘
┌ execution logs ───────────────────────────────────────────────────────┐
│ 02:31:01 [INFO]    propose_config — Groq llama-3.3-70b-versatile        │
│ 02:31:03 [WAITING] hitl_review — awaiting human approval                │
│ 02:31:40 [RESUMED] hitl_review — modify {SHOULD_LINEMERGE: true→false}  │
│ 02:31:42 [INFO]    preview_ingest — staged → steward_preview            │
│ 02:31:50 [INFO]    verify_parse — 14 events (was 1), ts_ok 93%          │
│ 02:31:55 [SUCCESS] apply_config — props.conf [app:log:multiformat]      │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 8. Where it's stored — ELI5

**Recommendation: SQLite (a single `steward.db` file).**

Think of SQLite as **one notebook file you can both write rows into and ask questions
of** — "show me every failed run", "what's the latest waiting run" — *without setting
up any server*. It's built into Python (no install), it's just a file (copy it, commit
a sample, delete it to reset), and it survives restarts so a paused HITL run is still
there tomorrow. For a history table + diffs + resumable runs, it's the lowest-effort
thing that is also genuinely robust.

The two alternatives and why not (for now):
- **Log into Splunk itself** (`steward_audit` index) — lovely story ("Steward watches
  Steward"), and we can add it later as a *mirror*. But it couples your audit log and
  paused runs to Splunk being up, and querying-back over MCP is heavier than a local file.
- **JSON file per run** — dead simple, but you hand-write all the sorting/filtering the
  history table needs. Fine for v0, but you outgrow it the moment you want "all waiting runs".

> Start with SQLite. If the dogfood story is worth it for the demo, add an optional
> **mirror** that also writes each finished run as an event into a `steward_audit`
> index — best of both, and querying your own audit trail *in Splunk via Steward* is a
> great closing beat.

### Schema (SQLite)
```
runs(          run_id TEXT PK, workflow TEXT, status TEXT,
               started_at TEXT, finished_at TEXT,
               input_json TEXT, output_json TEXT )

node_runs(     run_id TEXT, node_id TEXT, kind TEXT, status TEXT,
               started_at TEXT, finished_at TEXT )

logs(          run_id TEXT, ts TEXT, level TEXT, message TEXT )

hitl_requests( run_id TEXT, node_id TEXT, prompt TEXT, options_json TEXT,
               decision TEXT, patch_json TEXT, decided_at TEXT )   -- the pause/resume

changes(       run_id TEXT, conf TEXT, stanza TEXT, app TEXT,
               before_text TEXT, after_text TEXT,
               events_before INT, events_after INT,
               ts_ok_pct REAL, fields_extracted INT,
               modified_by_human INT )                              -- what changed & improved
```

---

## 9. Engine shape (functionality, not final code)

```python
# A node returns its output, OR pauses for a human.
@dataclass
class Node:
    id: str
    kind: str           # R | L | C | H | W
    run: Callable       # (ctx) -> output            for non-HITL nodes
    ask: Callable = None # (ctx) -> {prompt, options} for HITL nodes

class Workflow:         # nodes + edges (with branch labels: approved/modify/reject)
    ...

# The two engine entry points — everything else is a view over the store.
engine.start(workflow, input)  -> Run   # runs until done, failed, or a HITL 'waiting'
engine.resume(run_id, decision) -> Run  # applies approve/modify/reject, continues

# Decision shape
decision = {"action": "approve" | "modify" | "reject", "patch": {...}?}
```

Key property: `start()` and `resume()` both **drive the same loop** — pop the next
ready node, run it, record status+logs; if it's a HITL node, checkpoint and return
`waiting`. Resume just reloads the checkpoint and re-enters the loop.

---

## 10. Build order

1. **Engine + store + HITL node** *(priority)* — `steward/workflow.py` (engine, Run,
   Node, statuses) and `steward/runstore.py` (SQLite). Port `onboarding` to a graph
   with `hitl_review` + `hitl_apply` as real pause/resume nodes. Prove it headlessly:
   `start → waiting → resume(modify) → waiting → resume(approve) → ok`.
2. **Run history + change records** — `verify_parse` computes the metrics; `record_change`
   writes the diff. History becomes a query over `runs`/`changes`.
3. **Modify-and-rerun** — wire the `modify` decision + the field-level provenance diff.
4. **UI (later, separate decision)** — first a thin Streamlit "Runs" + "Workflow"
   view over the engine; a React/canvas frontend remains an option once functionality
   is locked.

> Everything in §1–§9 is storage- and UI-agnostic. The engine is the product; the
> canvas and the table are just two windows onto its runs.
