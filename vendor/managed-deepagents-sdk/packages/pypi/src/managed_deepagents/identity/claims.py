"""Claim-name resolution shared by authoring validation and the runtime token
mapper, so both agree on which claim supplies each envelope field.

Mirrors ``packages/npm/src/identity/claims.ts``.
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = ["normalize_groups", "user_claim_name"]


def user_claim_name(mapping: Mapping[str, object]) -> str | None:
    """The claim supplying the caller id, or ``None`` when neither is mapped."""
    return _non_empty(mapping.get("user"))


def normalize_groups(value: object) -> list[str]:
    """Normalize a ``groups`` claim (or header) value into a list.

    IdPs are inconsistent here: Auth0 and Okta send an array, Entra sends an
    array of object ids, and some proxies flatten to a delimited string. Accept
    all three rather than making authors pre-process the claim.
    """
    if isinstance(value, str):
        return value.replace(",", " ").split()
    if isinstance(value, (list, tuple)):
        return [text for entry in value if (text := str(entry).strip())]
    return []


def _non_empty(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
