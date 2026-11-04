"""Browser sessions MCP results carried, kept in the run's memory for ``browser_run``.

The model sees only a handle; each session is installed at most once, and a run that
never installs one drops it when it ends.
"""

from pydantic import JsonValue

from rt.backend import BackendCallError, OpenSweBackend, RunLocal


class HeldSessions(dict[str, dict[str, JsonValue]]):
    """The run's sessions, by handle."""

    @classmethod
    def of(cls, backend: OpenSweBackend) -> HeldSessions | None:
        """The sessions of the run in progress; None outside an Open SWE run."""
        try:
            return _HELD.get(backend.run)
        except BackendCallError:
            return None


_HELD: RunLocal[HeldSessions] = RunLocal(HeldSessions)
