# Splunk Steward — Setup Guide

This guide gets your environment ready to build and run **Splunk Steward**, an AI operations copilot that fills the missing-Splunk-expert gap. Everything below is free for development use.

Complete the steps in order — each step's output feeds the next. Most take a few minutes; the Splunk container in Step 2 is the only slow one.

---

## Prerequisites at a glance

| Resource | Purpose | Cost |
|---|---|---|
| Docker Desktop | Hosts Splunk and the app | Free |
| Splunk Enterprise image (`splunk/splunk:latest`) | The data platform | Free (Trial / Free license) |
| Splunk MCP Server app (Splunkbase) | Agent read access to Splunk | Free |
| Groq API key | LLM inference (Llama etc.) — reasoning & generation engine | Free (no card) |
| Python 3.11+ | Backend, RAG, UI | Free |
| Sample log dataset | Drives the onboarding demo | You create it |
| GitHub repo | Home for all code & assets | Free |

---

## Step 1 — Install Docker Desktop

1. Download Docker Desktop from [docker.com](https://www.docker.com/products/docker-desktop/) and install it.
2. In settings, allocate at least **2 CPUs and 4 GB RAM** (Splunk is resource-heavy).
3. Verify the install:

   ```bash
   docker --version
   docker run hello-world
   ```

---

## Step 2 — Pull and run Splunk Enterprise

1. Pull the official image:

   ```bash
   docker pull splunk/splunk:latest
   ```

2. Start the container with an admin password (8+ characters) and license acceptance:

   ```bash
   docker run -d \
     -p 8000:8000 \
     -p 8089:8089 \
     -e SPLUNK_START_ARGS='--accept-license' \
     -e SPLUNK_PASSWORD='<password>' \
     --name splunk \
     splunk/splunk:latest
   ```

   > **10.x images:** add `-e SPLUNK_GENERAL_TERMS=--accept-sgt-current-at-splunk-com` to accept the current Splunk General Terms.

3. Wait 1–2 minutes for startup, then log in at **http://localhost:8000** as `admin` with your password.
4. Confirm the **management API** responds on port **8089** — your code uses this endpoint for writes.

---

## Step 3 — (Optional) Switch to the Free license

The container starts on a **30-day Trial** license by default. If your project will outlive that window:

1. In the Splunk UI, go to **Settings → Licensing**.
2. Switch to **Splunk Free** (never-expiring, but ingest-limited).

For a hackathon the Trial is usually sufficient, so you can skip this step.

---

## Step 4 — Create a Splunk.com account and download the MCP Server app

1. Create a free account at [splunk.com](https://www.splunk.com) (required for Splunkbase downloads).
2. Go to the **Splunk MCP Server** app on Splunkbase (app **7931**).
3. Download the package (`.spl` / `.tgz`).

The MCP Server (GA, v1.0.0+) is the supported way to connect AI agents to Splunk data over streamable HTTP.

---

## Step 5 — Install the MCP Server app into your container

1. In the Splunk UI, go to **Apps → Manage Apps → Install app from file**.
2. Upload the package from Step 4.
3. Restart Splunk when prompted.

> Alternative: mount the app into the container's `etc/apps` directory via your `docker-compose.yml` later.

---

## Step 6 — Configure MCP access (auth + role + token)

1. Enable **token authentication** for the instance.
2. Create a role (e.g., `mcp_user`) and grant it the **`mcp_tool_execute`** capability plus API access. (The MCP Server app adds this capability for role-based access control.)
3. Create a user with that role.
4. Generate an **authentication token** and save it for Step 8.

> **Note:** The AI SPL tools (e.g., `generate_spl`) require the Splunk AI Assistant to be installed and need cloud connectivity. You can skip those — Splunk Steward uses MCP only for **reads** (`splunk_run_query`, `splunk_get_knowledge_objects`) and the LLM (via Groq) for reasoning.

---

## Step 7 — Create a Groq API key (free)

1. Sign up at the [Groq Console](https://console.groq.com) with email, GitHub, or Google — **no credit card required**.
2. Click **API Keys** in the left sidebar and create a key.
3. Save it for Step 8 — **never commit it to source control.**

> **Models & limits:** Groq's free tier serves open models through an OpenAI-compatible endpoint at `https://api.groq.com/openai/v1`. Good choices for Steward: `llama-3.3-70b-versatile` for config-generation quality, or `llama-3.1-8b-instant` for speed. Free-tier rate limits apply per model (around 30 requests/min plus a daily token cap) and change over time — check `console.groq.com/settings/limits` for current values. A 70B model is the better default for generating parsing configs.

---

## Step 8 — Set up the Python environment

1. Install **Python 3.11+** and create a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   ```

2. Install dependencies:

   ```bash
   pip install groq mcp splunk-sdk chromadb streamlit python-dotenv
   ```

   (Swap `chromadb` for `faiss-cpu` if you prefer FAISS; use `requests` if not using `splunk-sdk`.)

3. Freeze them:

   ```bash
   pip freeze > requirements.txt
   ```

4. Create a `.env` file:

   ```ini
   SPLUNK_HOST=localhost
   SPLUNK_MGMT_PORT=8089
   SPLUNK_MCP_TOKEN=<token-from-step-6>
   GROQ_API_KEY=<key-from-step-7>
   GROQ_MODEL=llama-3.3-70b-versatile
   ```

---

## Step 9 — Create the sample dataset

Write a deliberately **messy log file** — non-standard timestamps, multi-line events, unusual delimiters — that the onboarding copilot will parse during the demo.

```
mkdir -p datasets
# add e.g. datasets/messy_app.log
```

Keep it in `/datasets` so judges can reproduce the result.

---

## Step 10 — Initialize the GitHub repo

1. Create a **public** repository.
2. Add an **MIT license**.
3. Clone it locally.
4. Add a `.gitignore` that excludes secrets:

   ```gitignore
   .env
   .secrets/
   .venv/
   __pycache__/
   ```

This repo is the home for everything you build in the Part B phases.

---

## You're ready

After Step 10 you have:

- A running **Splunk Enterprise** instance
- An **agent-accessible data path** via the MCP Server
- A **Groq API key**
- A configured **Python environment**
- **Test data** for the demo
- A **public repo** with a license

This is everything the Part B build phases assume.
