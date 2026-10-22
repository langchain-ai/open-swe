"""Shared structural types for managed memory wiring.

Backend contracts come from Deep Agents (``deepagents.backends.protocol``);
only the dynamic result-path boundary stays structural here.
"""

from __future__ import annotations

from typing import Protocol


class MutablePathResult(Protocol):
    path: str
