"""Write path — apply .conf changes and ingest data via the Splunk REST API.

The MCP Server is read-only by design, so writes go through the management API
on port 8089 with admin credentials (local dev only).

EVERY function here is a side effect. The UI must gate these behind explicit
human approval — never call apply_* without a confirmed diff.
"""

from __future__ import annotations

import requests
import urllib3

from .config import CONFIG

# Local Docker uses a self-signed cert; silence the warning for dev.
if not CONFIG.verify_ssl:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_BASE = f"https://{CONFIG.splunk_host}:{CONFIG.splunk_mgmt_port}"
_AUTH = (CONFIG.splunk_admin_user, CONFIG.splunk_admin_password)


def _post(path: str, data: dict) -> requests.Response:
    resp = requests.post(
        f"{_BASE}{path}",
        data={**data, "output_mode": "json"},
        auth=_AUTH,
        verify=CONFIG.verify_ssl,
        timeout=30,
    )
    resp.raise_for_status()
    return resp


def apply_conf_stanza(conf: str, stanza: str, settings: dict[str, str]) -> dict:
    """Create/update a stanza in a .conf file (e.g. conf='props', stanza='my:sourcetype').

    Writes into CONFIG.target_app. Returns the API JSON response.
    """
    app = CONFIG.target_app
    base = f"/servicesNS/nobody/{app}/configs/conf-{conf}"
    # Try to create the stanza; if it exists, update its keys.
    try:
        _post(base, {"name": stanza, **settings})
    except requests.HTTPError:
        for key, value in settings.items():
            _post(f"{base}/{stanza}", {key: value})
    return {"conf": conf, "stanza": stanza, "settings": settings, "app": app}


def oneshot_ingest(filepath: str, index: str, sourcetype: str) -> dict:
    """One-shot upload a local file into Splunk so parsing can be previewed/verified.

    Use a throwaway index (e.g. 'steward_preview') so demo data stays isolated.
    """
    _post(
        "/services/data/inputs/oneshot",
        {"name": filepath, "index": index, "sourcetype": sourcetype},
    )
    return {"file": filepath, "index": index, "sourcetype": sourcetype}


def ensure_index(name: str) -> dict:
    """Create an index if it does not already exist."""
    try:
        _post("/services/data/indexes", {"name": name})
    except requests.HTTPError:
        pass  # already exists
    return {"index": name}
