"""Splunk Steward — Streamlit UI.

Run:  source .venv/bin/activate && streamlit run app.py

Two tabs:
  - Onboarding: paste/upload a log -> Groq proposes parsing config -> review diff
    -> (approve) preview ingest -> (approve) apply config.
  - Health: run read-only diagnostics -> plain-language findings + suggested fixes.

Every side-effecting action (apply config, ingest) sits behind an explicit button
that only appears after the proposal is shown. Human-in-the-loop by construction.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from steward import health, onboarding, splunk_mcp
from steward.config import CONFIG

st.set_page_config(page_title="Splunk Steward", page_icon="🛠️", layout="wide")
st.title("🛠️ Splunk Steward")
st.caption("The Splunk expert you don't have on staff — grounded, and human-approved.")

# --- Connection status strip -------------------------------------------------
with st.sidebar:
    st.subheader("Connection")
    st.write(f"**Host:** {CONFIG.splunk_host}:{CONFIG.splunk_mgmt_port}")
    st.write(f"**Model:** {CONFIG.groq_model}")
    if st.button("Test MCP connection"):
        st.success("MCP reachable ✅") if splunk_mcp.ping() else st.error("MCP unreachable ❌")

onboard_tab, health_tab = st.tabs(["📥 Data onboarding", "🩺 Health check"])

# --- Onboarding --------------------------------------------------------------
with onboard_tab:
    st.subheader("Onboard a log source")
    default_sample = ""
    sample_path = Path("datasets/messy_app.log")
    if sample_path.exists():
        default_sample = sample_path.read_text(errors="replace")

    log_text = st.text_area(
        "Paste a log sample (or load datasets/messy_app.log)",
        value=default_sample,
        height=200,
    )

    if st.button("Generate parsing config", type="primary", disabled=not log_text.strip()):
        with st.spinner("Reading the sample and inferring parsing rules…"):
            st.session_state["proposal"] = onboarding.propose_config(log_text)

    proposal = st.session_state.get("proposal")
    if proposal:
        col1, col2 = st.columns([3, 2])
        with col1:
            st.markdown("**Proposed `props.conf`**")
            st.code(proposal.as_conf_text(), language="ini")
        with col2:
            st.metric("Confidence", proposal.confidence)
            st.markdown(f"**Why:** {proposal.rationale}")

        st.divider()
        st.markdown("##### Human approval required")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("👁️ Preview ingest (throwaway index)"):
                with st.spinner("Ingesting sample into steward_preview…"):
                    info = onboarding.preview_ingest(str(sample_path), proposal)
                st.success(f"Ingested. Verify with: `{info['verify_spl']}`")
        with c2:
            if st.button("✅ Approve & apply config"):
                with st.spinner("Writing props.conf via REST…"):
                    result = onboarding.apply_proposal(proposal)
                st.success(f"Applied stanza [{result['applied']}]")

# --- Health ------------------------------------------------------------------
with health_tab:
    st.subheader("Operational health check")
    st.caption("Read-only diagnostics. Suggested fixes are proposals — nothing is applied.")
    if st.button("Run health check", type="primary"):
        with st.spinner("Running diagnostics and summarizing…"):
            st.session_state["findings"] = health.run_health_check()

    for f in st.session_state.get("findings", []):
        icon = {"ok": "✅", "info": "ℹ️", "warning": "⚠️", "critical": "🔴"}.get(f.severity, "•")
        with st.expander(f"{icon} {f.check} — {f.severity}"):
            st.write(f.finding)
            if f.suggested_fix:
                st.markdown(f"**Suggested fix:** {f.suggested_fix}")
            if f.rows:
                st.dataframe(f.rows)
