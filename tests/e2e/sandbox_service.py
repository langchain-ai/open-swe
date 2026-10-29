"""The E2E's stand-in for LangSmith's sandbox service.

In production LangSmith runs every sandbox: Managed Deep Agents creates each
thread's box, and the backend reconnects to it by id. The E2E fakes that SaaS
boundary as it does GitHub and Slack. A box is a shell on this machine, rooted in
the E2E's work directory, under the id its creator gave it. MDA and the backend
run as separate processes and share nothing but the id, so either side reaches
the same box from it.

``E2E_REAL_SANDBOX=1`` leaves the boundary real, for specs that need LangSmith.
Imports only ``deepagents``, which both processes' environments have.
"""

import hashlib
import os
import uuid
from pathlib import Path

from deepagents.backends import LocalShellBackend

REAL = os.environ.get("E2E_REAL_SANDBOX") == "1"

# Model keys stay out of the box, as a LangSmith sandbox never sees them.
_EXCLUDED_ENV = {
    "ANTHROPIC_API_KEY",
    "BASETEN_API_KEY",
    "FIREWORKS_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "LANGSMITH_API_KEY",
    "OPENAI_API_KEY",
}


class StandInSandbox(LocalShellBackend):
    """A box under ``sandbox_id``, which every process opening that id shares."""

    def __init__(self, sandbox_id: str) -> None:
        root = Path(os.environ["E2E_SANDBOX_ROOT"])
        root.mkdir(parents=True, exist_ok=True)
        super().__init__(
            root_dir=root,
            virtual_mode=True,
            inherit_env=False,
            env={key: value for key, value in os.environ.items() if key not in _EXCLUDED_ENV},
        )
        self._stand_in_id = sandbox_id

    @property
    def id(self) -> str:
        return self._stand_in_id


def open_box(sandbox_id: str | None = None) -> StandInSandbox:
    """The box ``sandbox_id`` names, or a new one."""
    return StandInSandbox(sandbox_id or f"e2e-{uuid.uuid4().hex[:12]}")


def mda_box_name(scope_key: str) -> str:
    """The name MDA gives the box it keeps for ``scope_key``, the same on every call."""
    return f"e2e-{hashlib.sha256(scope_key.encode()).hexdigest()[:16]}"
