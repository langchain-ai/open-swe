"""Service tokens: how a deployment's own service starts runs in a channel conversation.

A channel whose ``on_event`` names an endpoint hands every event to a service of
yours, which decides what the run gets. That service shares ``MDA_SERVICE_SECRET``
with the deployment: MDA signs each endpoint call with it. With a service token,
the same secret also lets the service act on one channel conversation through
the Agent Server's own API, for example to start a run there when a person
follows up somewhere else:

```python
from managed_deepagents.service_token import SERVICE_TOKEN_HEADER, mint_service_token

token = mint_service_token(thread_id=thread["thread_id"], owner=thread["metadata"]["owner"])
await client.runs.create(thread_id, "agent", input=..., headers={SERVICE_TOKEN_HEADER: token})
```

A token names one thread and the conversation that owns it (the thread's
``owner`` metadata), and lasts at most five minutes. It lets its holder read that
thread, start runs on it, and cancel them, as ``mda:service``; nothing else. The
thread keeps its owner, so the conversation's later messages still reach it.

The signing key is derived from each ``MDA_SERVICE_SECRET`` key for this purpose
only, so an endpoint call's signature can never stand in for a token.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Final
from uuid import UUID

__all__ = ["SERVICE_TOKEN_HEADER", "SERVICE_USER", "mint_service_token"]

SERVICE_TOKEN_HEADER: Final = "x-mda-service-token"  # noqa: S105 - a header name, not a secret
"""The request header that carries a service token."""
SERVICE_USER: Final = "mda:service"
"""The principal a service token's requests run as."""

_PURPOSE: Final = b"mda-service-token:v1"
_ISSUER: Final = "mda-service"
_AUDIENCE: Final = "mda-agent-server"
_ALGORITHM: Final = "HS256"
_MAX_LIFETIME_SECONDS: Final = 300
_DEFAULT_LIFETIME_SECONDS: Final = 60
_LEEWAY_SECONDS: Final = 30
_CHANNEL_THREAD_OWNER_PREFIX: Final = "mda:source-thread:"


@dataclass(frozen=True)
class ServiceGrant:
    """What a verified service token lets its holder do: act on one thread."""

    thread_id: str
    owner: str
    provider: str
    source_thread_key: str


class ServiceTokenError(ValueError):
    """A service token that can't be trusted; the reason is never shown to the caller."""


def mint_service_token(
    *, thread_id: str, owner: str, lifetime_seconds: int = _DEFAULT_LIFETIME_SECONDS
) -> str:
    """A token that lets the bearer act on ``thread_id``, owned by the conversation ``owner``.

    ``owner`` is the thread's ``owner`` metadata, ``mda:source-thread:<provider>:<key>``.
    Signs with the first key in ``MDA_SERVICE_SECRET``; raises ``ValueError`` without
    one, for a malformed thread id or owner, or a lifetime outside 1 to 300 seconds.
    """
    if not 0 < lifetime_seconds <= _MAX_LIFETIME_SECONDS:
        raise ValueError(f"lifetime_seconds must be 1 to {_MAX_LIFETIME_SECONDS}")
    _split_owner(owner)
    keys = _token_keys()
    if not keys:
        raise ValueError("service tokens need MDA_SERVICE_SECRET")
    import jwt  # noqa: PLC0415 - the Agent Server ships PyJWT; plain installs needn't

    now = int(time.time())
    claims = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "sub": _canonical_thread_id(thread_id),
        "owner": owner,
        "iat": now,
        "exp": now + lifetime_seconds,
    }
    return jwt.encode(claims, keys[0], algorithm=_ALGORITHM)


def verify_service_token(token: str) -> ServiceGrant:
    """The grant a token carries; raises ``ServiceTokenError`` for one MDA can't trust."""
    import jwt  # noqa: PLC0415 - the Agent Server ships PyJWT; plain installs needn't

    keys = _token_keys()
    if not keys:
        raise ServiceTokenError("no MDA_SERVICE_SECRET")
    claims: dict[str, object] | None = None
    for key in keys:
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[_ALGORITHM],
                audience=_AUDIENCE,
                issuer=_ISSUER,
                leeway=_LEEWAY_SECONDS,
                options={"require": ["iss", "aud", "sub", "iat", "exp"]},
            )
            break
        except jwt.InvalidSignatureError:
            continue
        except jwt.PyJWTError as error:
            raise ServiceTokenError(type(error).__name__) from None
    if claims is None:
        raise ServiceTokenError("signature")
    issued, expires = claims.get("iat"), claims.get("exp")
    if not isinstance(issued, int) or not isinstance(expires, int):
        raise ServiceTokenError("times")
    if expires - issued > _MAX_LIFETIME_SECONDS or issued > time.time() + _LEEWAY_SECONDS:
        raise ServiceTokenError("lifetime")
    subject, owner = claims.get("sub"), claims.get("owner")
    if not isinstance(subject, str) or not isinstance(owner, str):
        raise ServiceTokenError("claims")
    try:
        thread_id = _canonical_thread_id(subject)
        provider, source_thread_key = _split_owner(owner)
    except ValueError:
        raise ServiceTokenError("claims") from None
    return ServiceGrant(
        thread_id=thread_id, owner=owner, provider=provider, source_thread_key=source_thread_key
    )


def _token_keys() -> tuple[bytes, ...]:
    """The keys tokens are signed with: one per ``MDA_SERVICE_SECRET`` key, for tokens only."""
    # Lazy: the endpoint module imports channel types this module must not load.
    from managed_deepagents._channels.http.endpoint import _signing_keys  # noqa: PLC0415

    return tuple(hmac.new(key, _PURPOSE, hashlib.sha256).digest() for key in _signing_keys())


def _canonical_thread_id(thread_id: str) -> str:
    try:
        return str(UUID(thread_id))
    except (TypeError, ValueError):
        raise ValueError("thread_id must be a UUID") from None


def _split_owner(owner: str) -> tuple[str, str]:
    """A channel conversation owner's provider and source thread key."""
    # Lazy: avoid import cycles at auth module load.
    from managed_deepagents._channels.source_providers import (  # noqa: PLC0415
        is_trusted_source_provider,
    )

    if not owner.startswith(_CHANNEL_THREAD_OWNER_PREFIX):
        raise ValueError("owner must be a channel conversation, mda:source-thread:<provider>:<key>")
    provider, _, key = owner.removeprefix(_CHANNEL_THREAD_OWNER_PREFIX).partition(":")
    if not provider or not key or not is_trusted_source_provider(provider):
        raise ValueError("owner must name a channel provider and a conversation key")
    return provider, key
