#!/usr/bin/env python3
"""Smoke test for Splunk Steward setup.

Validates the two external dependencies end-to-end:
  1. Splunk MCP Server  -- initialize, tools/list, and a sample splunk_run_query
  2. Groq LLM           -- a minimal chat completion

Run:  source .venv/bin/activate && python mcp_smoke_test.py
"""
import json
import os
import re
import sys

import requests
import urllib3
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

MCP_URL = os.environ["SPLUNK_MCP_URL"]
MCP_TOKEN = os.environ["SPLUNK_MCP_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

HEADERS = {
    "Authorization": f"Bearer {MCP_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def mcp(method, params=None, _id=1):
    body = {"jsonrpc": "2.0", "id": _id, "method": method, "params": params or {}}
    r = requests.post(MCP_URL, headers=HEADERS, json=body, verify=False, timeout=60)
    r.raise_for_status()
    # Response may be SSE (text/event-stream) or plain JSON -- extract the JSON object.
    text = r.text
    m = re.search(r"\{.*\}", text, re.S)
    payload = json.loads(m.group(0) if m else text)
    if "error" in payload:
        raise RuntimeError(f"MCP {method} error: {payload['error']}")
    return payload["result"]


def test_splunk():
    info = mcp("initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "smoke-test", "version": "1.0"},
    })
    si = info["serverInfo"]
    print(f"[splunk] connected -> {si['name']} v{si['version']}")

    tools = mcp("tools/list", _id=2)["tools"]
    names = [t["name"] for t in tools]
    print(f"[splunk] {len(tools)} tools: {', '.join(names)}")
    for required in ("splunk_run_query", "splunk_get_knowledge_objects"):
        assert required in names, f"missing required tool: {required}"

    # Sample read: count internal events in the last 15 minutes.
    res = mcp("tools/call", {
        "name": "splunk_run_query",
        "arguments": {"query": "search index=_internal | stats count", "earliest_time": "-15m"},
    }, _id=3)
    snippet = json.dumps(res)[:200]
    print(f"[splunk] splunk_run_query OK -> {snippet}")


def test_groq():
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": "Reply with exactly: STEWARD_OK"}],
            "max_tokens": 10,
            "temperature": 0,
        },
        timeout=30,
    )
    r.raise_for_status()
    reply = r.json()["choices"][0]["message"]["content"].strip()
    print(f"[groq]   {GROQ_MODEL} replied -> {reply!r}")


if __name__ == "__main__":
    ok = True
    for name, fn in (("Splunk MCP", test_splunk), ("Groq", test_groq)):
        try:
            fn()
        except Exception as e:
            ok = False
            print(f"[FAIL] {name}: {e}")
    print("\n" + ("ALL CHECKS PASSED ✅" if ok else "SOME CHECKS FAILED ❌"))
    sys.exit(0 if ok else 1)
