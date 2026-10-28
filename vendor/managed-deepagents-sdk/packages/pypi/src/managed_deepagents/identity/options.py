"""The simplified ``define_identity(...)`` input, and its normalization.

Authors describe how callers authenticate. The runtime supplies the managed
user-isolation policy, so credential ownership does not become an authoring knob.
Durable memory is not an identity concern at all — it has its own root
declaration in ``memory.py``.

Mirrors ``packages/npm/src/identity/options.ts``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypeAlias, cast

if TYPE_CHECKING:
    from managed_deepagents.identity import (
        HttpIngress,
        IdentityConfig,
        LangSmithApiKeyIngress,
        ValidatedTokenProvider,
    )

__all__ = [
    "IdentityAuth",
    "normalize_identity",
]

IdentityAuth: TypeAlias = 'Literal["backend"] | LangSmithApiKeyIngress | ValidatedTokenProvider | list[ValidatedTokenProvider]'  # noqa: E501
"""How MDA learns who is calling.

``"backend"`` means a backend you operate has already authenticated the caller
and asserts the user id over the reserved ingress headers.
``auth.langsmith_api_key()`` verifies a LangSmith workspace API key and treats
the result as a service identity. One or more ``providers.*`` entries mean MDA
verifies the caller's own token server-side.
"""


def normalize_identity(
    *,
    auth: IdentityAuth,
) -> IdentityConfig:
    """Derive the resolved :class:`IdentityConfig` from the declared options.

    Pure and total: every reachable combination produces a config that
    ``validate_identity`` accepts, so the errors that exist only to police two
    fields agreeing with each other are unreachable from this path.

    Threads are always user-owned, and durable memory is declared by
    ``memory.py``, so credentials are the only axis left for identity to resolve.
    """
    return {
        "ingress": {"http": _resolve_ingress(auth)},
        "scope": {"credentials": "user"},
    }


def _resolve_ingress(auth: IdentityAuth) -> HttpIngress:
    if auth == "backend":
        return "backend"
    if isinstance(auth, str):
        raise ValueError(
            f"define_identity(...) unknown `auth` value {auth!r}. "
            "Use 'backend', auth.langsmith_api_key(), or one or more providers.* entries."
        )
    if isinstance(auth, dict) and auth.get("mode") == "langsmith_api_key":
        return cast("LangSmithApiKeyIngress", auth)
    providers = cast(
        "list[ValidatedTokenProvider]",
        list(auth) if isinstance(auth, list) else [auth],
    )
    return {"mode": "validated_token", "providers": providers}
