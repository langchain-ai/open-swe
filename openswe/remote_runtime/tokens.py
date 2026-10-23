"""Run tokens: the identity a remotely hosted run carries back to this backend.

Dispatch signs one per run and stamps it on the run's configurable. The remote
runtime sends it as a bearer token on every tool server call, and the tool server
reads the thread and run configuration and metadata from the verified claims, never
from tool arguments the model controls.
"""

import time
from collections.abc import Mapping
from typing import Final

import jwt
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError

from openswe.config import ENV

_ISSUER: Final = "open-swe"
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


class RemoteRun(BaseModel):
    """The run a token was signed for, exactly as dispatch stamped it."""

    model_config = ConfigDict(frozen=True)

    thread_id: str
    assistant_id: str
    configurable: dict[str, JsonValue]
    metadata: dict[str, JsonValue] = {}


def _signing_keys() -> list[str]:
    secret = ENV.REMOTE_RUNTIME_TOKEN_SECRET.optional() or ""
    return [key.strip() for key in secret.split(",") if key.strip()]


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
        "exp": issued_at + _TTL_SECONDS,
        "sub": run.thread_id,
        "assistant_id": run.assistant_id,
        "cfg": run.configurable,
        "md": run.metadata,
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
