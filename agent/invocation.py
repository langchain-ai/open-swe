"""Canonical invocation identity with rolling compatibility for legacy names."""

import uuid
from collections.abc import Mapping
from typing import Any

INVOCATION_ID_KEY = "invocation_id"
LEGACY_INVOCATION_ID_KEY = "prepare_run_id"


def resolve_invocation_id(*values: Mapping[str, Any] | None) -> str | None:
    """Resolve one invocation id, rejecting malformed or conflicting fields."""
    found: set[str] = set()
    for value in values:
        if value is None:
            continue
        for key in (INVOCATION_ID_KEY, LEGACY_INVOCATION_ID_KEY):
            if key not in value:
                continue
            candidate = value[key]
            if not isinstance(candidate, str) or not candidate:
                raise ValueError(f"{key} must be a non-empty string")
            found.add(candidate)
    if len(found) > 1:
        raise ValueError("invocation_id conflicts with prepare_run_id")
    return next(iter(found), None)


def new_invocation_id() -> str:
    """Create an application-owned invocation identifier."""
    return str(uuid.uuid4())


def with_invocation_id(value: Mapping[str, Any] | None, invocation_id: str) -> dict[str, Any]:
    """Write both names until all workers use invocation_id and rollback no longer needs the alias.

    Keep legacy reads for queued jobs, callbacks, and historical metadata.
    """
    if not invocation_id:
        raise ValueError("invocation_id must be a non-empty string")
    result = dict(value or {})
    existing = resolve_invocation_id(result)
    if existing is not None and existing != invocation_id:
        raise ValueError("invocation_id conflicts with existing invocation identity")
    result[INVOCATION_ID_KEY] = invocation_id
    result[LEGACY_INVOCATION_ID_KEY] = invocation_id
    return result
