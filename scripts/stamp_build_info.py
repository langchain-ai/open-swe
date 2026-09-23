#!/usr/bin/env python3
"""Stamp the backend's build-identity sidecar at the path given as argv[1].

Writes the UTC build time and optional SOURCE_COMMIT as
open-swe-build-info.json under the given directory, creating it first: custom
dockerfile_lines run before the source copy, so the agent package is not on disk
yet and the sidecar cannot live inside it.
"""

import json
import os
import sys
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

directory = Path(sys.argv[1])
directory.mkdir(parents=True, exist_ok=True)
commit = os.environ.get("SOURCE_COMMIT", "").strip()
info = {"built_at": datetime.now(UTC).isoformat()}
if commit:
    info["commit"] = commit
if len(sys.argv) > 2:
    dashboard_stamp = Path(sys.argv[2]) / "open-swe-build-info.json"
    if dashboard_stamp.is_file():
        info["dashboard_stamp_sha256"] = sha256(dashboard_stamp.read_bytes()).hexdigest()
(directory / "open-swe-build-info.json").write_text(json.dumps(info))
