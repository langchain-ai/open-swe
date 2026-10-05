"""Expected tool failures with recovery and partial-operation context."""

from collections.abc import Mapping


class ToolError(Exception):
    """A tool failed without completing its requested operation."""

    def __init__(self, message: str, *, details: Mapping[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})
