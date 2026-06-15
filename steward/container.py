"""Local-dev helper: stage a host file inside the Splunk container.

Splunk runs in Docker and reads from the *container* filesystem, not the host,
so its one-shot ingest endpoint cannot see paths like ./datasets/messy_app.log.
For preview ingest we copy the sample into the container with `docker cp`.

This is a development convenience only. Production onboarding would use a
universal forwarder or HEC instead of copying files into the indexer.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import CONFIG


def stage_file(local_path: str) -> str:
    """Copy a host file into the Splunk container; return the container-side path.

    Raises a clear error if the file is missing or the docker CLI is unavailable.
    """
    src = Path(local_path)
    if not src.exists():
        raise FileNotFoundError(f"Sample file not found on host: {local_path}")
    if shutil.which("docker") is None:
        raise RuntimeError(
            "docker CLI not found on host — cannot stage the sample into the "
            "Splunk container. Run the app on the same host as your container."
        )

    container = CONFIG.splunk_container
    dest_dir = CONFIG.container_data_dir
    target = f"{dest_dir}/{src.name}"

    # Ensure the staging dir exists, then copy the file in.
    subprocess.run(
        ["docker", "exec", container, "mkdir", "-p", dest_dir],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["docker", "cp", str(src), f"{container}:{target}"],
        check=True, capture_output=True,
    )
    return target
