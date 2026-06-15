# 🛠️ Splunk Steward

**The Splunk expert you don't have on staff** — an AI operations copilot that
onboards messy log sources and diagnoses operational health, grounded in your real
Splunk instance and gated behind human approval.

Steward reads from Splunk through the **MCP Server** (never mutating state), reasons
with a **Groq-hosted Llama model**, and applies changes through the **REST API** only
after a human approves the diff.

---

## What it does

- **📥 Data onboarding** — paste a messy log sample; Steward infers the `props.conf`
  needed to parse it cleanly (timestamps, line-breaking, field extraction), shows you
  the config and its rationale, lets you **preview the parse against a throwaway
  index**, and applies it only on your approval.
- **🩺 Health check** — runs the read-only diagnostics a senior admin would (skipped
  searches, license usage, indexing errors) and turns the raw numbers into
  plain-language findings with suggested fixes. Nothing is applied.

Every side effect is human-approved. The LLM only *proposes* — see
[ARCHITECTURE.md](ARCHITECTURE.md) for the trust boundaries.

---

## Quickstart

> Full environment setup (Docker Splunk, MCP Server app, role/token, Groq key) is in
> **[SETUP.md](SETUP.md)**. Do that first — it produces the `.env` this app reads.

```bash
# 1. Activate the environment created in SETUP.md
source .venv/bin/activate

# 2. Sanity-check both dependencies (Splunk MCP + Groq)
python mcp_smoke_test.py        # expect: ALL CHECKS PASSED ✅

# 3. Launch the app
streamlit run app.py
```

Then in the UI:
1. **Sidebar → Test MCP connection** — confirm the read path is live.
2. **Onboarding tab** — load `datasets/messy_app.log`, **Generate parsing config**.
3. **Preview ingest** — stages the sample into the `steward_preview` index; run the
   `verify_spl` it prints to see the parsed events.
4. **Approve & apply** — writes the `props.conf` stanza.
5. **Health tab → Run health check** — read-only diagnostics in plain language.

---

## Demo narrative (the "why human-in-the-loop" beat)

The included sample [`datasets/messy_app.log`](datasets/messy_app.log) is deliberately
nasty — five timestamp formats, multi-line events, odd delimiters. Steward proposes a
config, the **preview shows the real parse**, you approve, it applies. Because the
human sees the parsed result before applying, a bad LLM suggestion gets caught at
preview — not in production. That review loop is the point.

---

## Layout

```
app.py                 Streamlit UI (entrypoint)
steward/               Core package
  config.py            .env-driven config (single source of truth)
  llm_groq.py          Groq LLM wrapper (reasoning engine)
  splunk_mcp.py        READ path — query Splunk via MCP Server
  splunk_rest.py       WRITE path — apply .conf / ingest via REST
  container.py         Dev-only — docker cp staging for preview ingest
  onboarding.py        propose → preview → apply flow
  health.py            Read-only diagnostics → plain-language findings
datasets/messy_app.log Sample log for the onboarding demo
mcp_smoke_test.py      End-to-end health check (MCP + Groq)
SETUP.md               Environment setup guide
ARCHITECTURE.md        System design, paths, trust boundaries
```

---

## Configuration

All config is read from `.env` (created during [SETUP.md](SETUP.md)). Key variables:

| Variable | Purpose |
|---|---|
| `SPLUNK_MCP_URL` / `SPLUNK_MCP_TOKEN` | Read path — MCP endpoint + bearer token |
| `SPLUNK_HOST` / `SPLUNK_MGMT_PORT` / `SPLUNK_ADMIN_PASSWORD` | Write path — REST API |
| `SPLUNK_VERIFY_SSL` | TLS verify (`false` for the local self-signed cert) |
| `GROQ_API_KEY` / `GROQ_MODEL` | Reasoning engine (default `llama-3.3-70b-versatile`) |
| `STEWARD_TARGET_APP` | Splunk app config is written into (default `search`) |
| `STEWARD_SPLUNK_CONTAINER` / `STEWARD_CONTAINER_DATA_DIR` | Dev preview staging |

---

## Tech stack

Python 3.11+ · [Streamlit](https://streamlit.io) UI ·
[Groq](https://groq.com) (Llama 3.3 70B) · Splunk Enterprise +
[MCP Server](https://splunkbase.splunk.com/app/7931) · the
[MCP](https://modelcontextprotocol.io) Python SDK.

> **Note:** the preview ingest copies files into the Splunk container via `docker cp`
> for local-dev convenience. Production onboarding would use a Universal Forwarder or
> HEC. See [ARCHITECTURE.md](ARCHITECTURE.md#dev-only-vs-production).

## License

[MIT](LICENSE)
