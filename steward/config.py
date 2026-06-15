"""Central config — loads everything from the environment (.env).

Keep all secrets and host details here so the rest of the package never
reads os.environ directly. Mirrors the keys defined in SETUP.md Step 8.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(
            f"Missing required env var: {key}. "
            f"Set it in your .env (see SETUP.md Step 8)."
        )
    return val


@dataclass(frozen=True)
class Config:
    # --- Splunk connection ---
    splunk_host: str = os.getenv("SPLUNK_HOST", "localhost")
    splunk_mgmt_port: int = int(os.getenv("SPLUNK_MGMT_PORT", "8089"))
    # MCP Server streamable-HTTP endpoint (read path).
    # NOTE: confirm this path against your MCP Server v1.2.0 install — it is
    # whatever your working mcp_smoke_test.py connects to.
    mcp_url: str = os.getenv(
        "SPLUNK_MCP_URL",
        f"https://{os.getenv('SPLUNK_HOST', 'localhost')}:{os.getenv('SPLUNK_MGMT_PORT', '8089')}/services/mcp/",
    )
    mcp_token: str = os.getenv("SPLUNK_MCP_TOKEN", "")

    # Admin creds for the REST write path. For local dev only.
    splunk_admin_user: str = os.getenv("SPLUNK_ADMIN_USER", "admin")
    splunk_admin_password: str = os.getenv("SPLUNK_ADMIN_PASSWORD", "")

    # Self-signed cert in local Docker — verification off for dev only.
    verify_ssl: bool = os.getenv("SPLUNK_VERIFY_SSL", "false").lower() == "true"

    # --- Groq LLM ---
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # App where Steward writes config (its own app keeps changes isolated).
    target_app: str = os.getenv("STEWARD_TARGET_APP", "search")

    # Local-dev only: name of the Splunk Docker container and a writable dir
    # inside it where preview samples are staged before one-shot ingest.
    splunk_container: str = os.getenv("STEWARD_SPLUNK_CONTAINER", "splunk")
    container_data_dir: str = os.getenv("STEWARD_CONTAINER_DATA_DIR", "/tmp/steward_datasets")

    def validate(self) -> None:
        _require("SPLUNK_MCP_TOKEN")
        _require("GROQ_API_KEY")


CONFIG = Config()
