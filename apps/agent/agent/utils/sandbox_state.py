"""Shared sandbox state used by server and middleware."""

from __future__ import annotations

from typing import Any

# Thread ID -> SandboxBackend mapping, shared between server.py and middleware
SANDBOX_BACKENDS: dict[str, Any] = {}
