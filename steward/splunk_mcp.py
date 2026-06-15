"""Read path — query Splunk through the MCP Server.

This mirrors the connection your working mcp_smoke_test.py already uses. If your
smoke test sets up the session differently (custom SSL context, different tool
names, etc.), copy that setup here so the two stay in sync.

The MCP client is async; we expose thin synchronous wrappers (asyncio.run) so the
Streamlit layer can stay simple.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .config import CONFIG

_HEADERS = {"Authorization": f"Bearer {CONFIG.mcp_token}"}


def _http_client_factory(headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
    """Build the httpx client the MCP SDK uses, honoring CONFIG.verify_ssl.

    Local Splunk runs on a self-signed cert, so verification must be off for dev
    (this mirrors the verify=False your mcp_smoke_test.py relies on)."""
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout if timeout is not None else httpx.Timeout(30.0),
        auth=auth,
        verify=CONFIG.verify_ssl,
        follow_redirects=True,
    )


async def _call_tool(name: str, args: dict[str, Any]) -> Any:
    async with streamablehttp_client(
        CONFIG.mcp_url, headers=_HEADERS, httpx_client_factory=_http_client_factory
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, args)
            # MCP returns content blocks; collect text payloads.
            parts: list[str] = []
            for block in getattr(result, "content", []) or []:
                text = getattr(block, "text", None)
                if text:
                    parts.append(text)
            return "\n".join(parts)


def _run(coro):
    return asyncio.run(coro)


def run_query(spl: str, earliest: str = "-15m", latest: str = "now") -> list[dict]:
    """Run an SPL search via the MCP `splunk_run_query` tool.

    Returns parsed result rows when the tool emits JSON; otherwise an empty list.
    Adjust the tool name/arg keys to match your MCP Server version if needed.
    """
    raw = _run(
        _call_tool(
            "splunk_run_query",
            {"query": spl, "earliest_time": earliest, "latest_time": latest},
        )
    )
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "results" in data:
            return data["results"]
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def get_knowledge_objects() -> str:
    """Inventory knowledge objects via the MCP `splunk_get_knowledge_objects` tool."""
    return _run(_call_tool("splunk_get_knowledge_objects", {}))


def ping() -> bool:
    """Lightweight connectivity check used by the smoke test / health tab."""
    try:
        run_query("| makeresults count=1")
        return True
    except Exception:
        return False
