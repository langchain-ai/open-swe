"""What a run may read and save of memory, checked on every file tool call.

A person's own layer is theirs alone: MDA only mounts it for them. Robin's layer is saved only
from a public channel, so nothing said in a DM, private channel or group DM reaches everyone.
Where outsiders may see the conversation, memory isn't read or saved at all. Nothing is
deleted.
"""

import posixpath
from collections.abc import Mapping
from typing import Final

from runtime_api.catalog import MemoryScope

from rt.auto_review import PATH_ARGUMENTS
from rt.prompts import prompt

_MEMORY: Final = "/memories"
_AGENT: Final = "/memories/agent"
_USER: Final = "/memories/user"
_WRITES: Final = frozenset({"write_file", "edit_file"})
_SEARCHES: Final = frozenset({"grep", "glob"})


class MemoryPolicy:
    """The memory calls a run with ``scope`` may make."""

    def __init__(self, scope: MemoryScope | None) -> None:
        # A run the backend gave no scope uses no memory.
        self._scope = scope or MemoryScope(thread="external")

    @staticmethod
    def path(name: str, arguments: Mapping[str, object]) -> str | None:
        """The path ``name``'s call acts on, rooted at ``/`` the way the file tools resolve it."""
        path = arguments.get(PATH_ARGUMENTS.get(name, ""))
        if not isinstance(path, str) or not path.strip():
            return None
        return posixpath.normpath("/" + path.strip().lstrip("/"))

    def refusal(self, name: str, arguments: Mapping[str, object]) -> str | None:
        """Why the call may not run, worded for the model, or None when it may."""
        path = self.path(name, arguments)
        if name in _SEARCHES and path in (None, "/"):
            path = _MEMORY
        if path is None or not (path == _MEMORY or path.startswith(_MEMORY + "/")):
            return None
        if self._scope.thread == "external":
            return self._refused("outsiders", path)
        if name == "delete":
            return self._refused("delete", path)
        if name not in _WRITES:
            return None
        if path.startswith(_USER + "/"):
            return None
        if not path.startswith(_AGENT + "/"):
            return self._refused("elsewhere", path)
        return None if self._scope.thread == "public" else self._refused("public", path)

    @staticmethod
    def _refused(kind: str, path: str) -> str:
        return prompt("memory/refusal", kind=kind, path=path)
