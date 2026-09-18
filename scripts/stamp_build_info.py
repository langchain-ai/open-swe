#!/usr/bin/env python3
"""Stamp the backend's build-identity sidecar at the path given as argv[1].

Writes ``{"commit": SOURCE_COMMIT}`` (or ``{}`` when SOURCE_COMMIT is unset) as
open-swe-build-info.json under the given directory, creating it first: custom
dockerfile_lines run before the source copy, so the agent package is not on disk
yet and the sidecar cannot live inside it.
"""

import json
import os
import sys
from pathlib import Path

directory = Path(sys.argv[1])
directory.mkdir(parents=True, exist_ok=True)
commit = os.environ.get("SOURCE_COMMIT", "")
(directory / "open-swe-build-info.json").write_text(
    json.dumps({"commit": commit} if commit else {})
)
