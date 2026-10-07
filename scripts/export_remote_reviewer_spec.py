"""Write the reviewer tool and subagent spec the Managed Deep Agents reviewer binds at build time.

Run after changing a reviewer tool's signature or description, or the reviewer subagent prompts:

    uv run python -m scripts.export_remote_reviewer_spec
"""

import json
from pathlib import Path

from openswe.remote_runtime.reviewer import runtime_spec

SPEC_PATH = Path(__file__).resolve().parents[1] / "mda/reviewer/open_swe_reviewer/spec.json"


def render_spec() -> str:
    return json.dumps(runtime_spec(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    SPEC_PATH.write_text(render_spec(), encoding="utf-8")
