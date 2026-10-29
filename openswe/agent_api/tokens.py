"""Run tokens: the identity a remotely hosted run carries back to this backend.

Dispatch signs one per run and stamps it on the run's configurable. The remote
runtime sends it as a bearer token on every tool server call, and the tool server
reads the thread and run configuration and metadata from the verified claims, never
from tool arguments the model controls.
"""

import hashlib
import hmac
import secrets
import time
from collections.abc import Mapping
from typing import Final, Self

import jwt
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError

from openswe.config import ENV

_ISSUER: Final = "open-swe"
# Kept from before the package was named the agent API: tokens of runs still
# queued when the name changed must keep verifying.
_AUDIENCE: Final = "open-swe-remote-runtime"
# A worker grant names the run that launched a worker, never the worker itself.
_GRANT_AUDIENCE: Final = "open-swe-worker-grant"
_ALGORITHM: Final = "HS256"
# Longer than a run can live: the platform ends background runs after a day, and an
# enqueued run can wait behind another before it starts.
_TTL_SECONDS: Final = 48 * 60 * 60
_LEEWAY_SECONDS: Final = 60
_REQUIRED_CLAIMS: Final = ("exp", "iat", "iss", "aud", "sub")
_text = TypeAdapter(str)


class RuntimeTokenError(Exception):
    """A token was refused; the message names the failed check, never the token."""


class RunSettings(BaseModel):
    """What the backend decided for a run as it started it, which every later call reads."""

    model_config = ConfigDict(frozen=True)

    # Where the run was asked from, once a default is applied.
    source: str | None = None
    # Whether pull requests the run opens start as drafts, by the sender's preference.
    draft_prs: bool | None = None
    # The model and effort the run calls, once settings and overrides are applied.
    model_id: str | None = None
    effort: str | None = None
    # The digest of every tool the run started with, kept in ``run_tool_sets``;
    # its catalog lists only those the model is offered.
    tools: str | None = None

    def configurable(self) -> dict[str, JsonValue]:
        """The run config keys these settings fill."""
        values: dict[str, JsonValue] = {
            "source": self.source,
            "draft_prs": self.draft_prs,
            "resolved_agent_model_id": self.model_id,
            "resolved_agent_effort": self.effort,
        }
        return {key: value for key, value in values.items() if value is not None}


class RemoteRun(BaseModel):
    """The run a token was signed for, exactly as dispatch stamped it."""

    model_config = ConfigDict(frozen=True)

    thread_id: str
    assistant_id: str
    configurable: dict[str, JsonValue]
    metadata: dict[str, JsonValue] = {}
    settings: RunSettings = RunSettings()
    # What the run's operation grants are signed under; None on a run started before
    # operations were granted, whose tools its plan bounds instead.
    grant_id: str | None = None
    # When the run's token expires, in epoch seconds; a token signed again for the run
    # keeps it, so holding one never extends it.
    expires_at: int | None = None

    def granting(self) -> Self:
        """This run, with a grant id of its own for the operations its catalog grants."""
        return self.model_copy(update={"grant_id": secrets.token_urlsafe(16)})

    def grant(self, operation: str) -> str | None:
        """The grant of ``operation`` to this run, which its calls of it must carry."""
        keys = _signing_keys()
        return None if self.grant_id is None or not keys else _grant(keys[0], self, operation)

    def granted(self, operation: str, grant: str | None) -> bool:
        """Whether this run may call ``operation``: its catalog granted it, under any live key."""
        return (
            self.grant_id is not None
            and grant is not None
            and any(
                hmac.compare_digest(_grant(key, self, operation), grant) for key in _signing_keys()
            )
        )


def _signing_keys() -> list[str]:
    secret = ENV.REMOTE_RUNTIME_TOKEN_SECRET.optional() or ""
    return [key.strip() for key in secret.split(",") if key.strip()]


def _grant(key: str, run: RemoteRun, operation: str) -> str:
    message = f"{run.thread_id}\n{run.grant_id}\n{operation}".encode()
    return hmac.new(key.encode(), message, hashlib.sha256).hexdigest()[:32]


def runtime_tokens_configured() -> bool:
    return bool(_signing_keys())


def _sign(audience: str, run: RemoteRun, extra: dict[str, JsonValue], now: int | None) -> str:
    keys = _signing_keys()
    if not keys:
        raise RuntimeTokenError("REMOTE_RUNTIME_TOKEN_SECRET is not configured")
    issued_at = int(time.time()) if now is None else now
    claims: dict[str, JsonValue] = {
        "iss": _ISSUER,
        "aud": audience,
        "iat": issued_at,
        "exp": run.expires_at or issued_at + _TTL_SECONDS,
        "sub": run.thread_id,
        "assistant_id": run.assistant_id,
        "cfg": run.configurable,
        "md": run.metadata,
        **({"set": settings} if (settings := run.settings.model_dump(exclude_none=True)) else {}),
        **({"gid": run.grant_id} if run.grant_id else {}),
        **extra,
    }
    return jwt.encode(claims, keys[0], algorithm=_ALGORITHM)


def _decode(token: str, audience: str) -> dict[str, object]:
    keys = _signing_keys()
    if not keys:
        raise RuntimeTokenError("REMOTE_RUNTIME_TOKEN_SECRET is not configured")
    for key in keys:
        try:
            return jwt.decode(
                token,
                key,
                algorithms=[_ALGORITHM],
                audience=audience,
                issuer=_ISSUER,
                options={"require": list(_REQUIRED_CLAIMS)},
                leeway=_LEEWAY_SECONDS,
            )
        except jwt.InvalidSignatureError:
            continue
        except jwt.PyJWTError as exc:
            raise RuntimeTokenError(type(exc).__name__) from exc
    raise RuntimeTokenError("InvalidSignatureError")


def _run(claims: Mapping[str, object]) -> RemoteRun:
    return RemoteRun.model_validate(
        {
            "thread_id": claims.get("sub"),
            "assistant_id": claims.get("assistant_id"),
            "configurable": claims.get("cfg"),
            "metadata": claims.get("md", {}),
            "settings": claims.get("set", {}),
            "grant_id": claims.get("gid"),
            "expires_at": claims.get("exp"),
        }
    )


def sign_runtime_token(run: RemoteRun, *, now: int | None = None) -> str:
    return _sign(_AUDIENCE, run, {}, now)


def verify_runtime_token(token: str) -> RemoteRun:
    claims = _decode(token, _AUDIENCE)
    try:
        return _run(claims)
    except ValidationError as exc:
        raise RuntimeTokenError("malformed claims") from exc


class WorkerGrant(BaseModel):
    """What a coordinator's run hands the async subagent it launches, in place of its own token.

    It lets the subagent read its coordinator's catalog and exchange the grant,
    with its own thread id, for a run token of its own. It authorizes nothing else.
    """

    model_config = ConfigDict(frozen=True)

    coordinator: RemoteRun
    # The coordinator's tool call that launched the worker: one grant, one worker.
    request_id: str


def sign_worker_grant(grant: WorkerGrant, *, now: int | None = None) -> str:
    return _sign(_GRANT_AUDIENCE, grant.coordinator, {"rid": grant.request_id}, now)


def verify_worker_grant(token: str) -> WorkerGrant:
    claims = _decode(token, _GRANT_AUDIENCE)
    try:
        return WorkerGrant(
            coordinator=_run(claims),
            request_id=_text.validate_python(claims.get("rid")),
        )
    except ValidationError as exc:
        raise RuntimeTokenError("malformed claims") from exc
