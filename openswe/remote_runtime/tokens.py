"""Run tokens: the identity a remotely hosted run carries back to this backend.

Dispatch signs one per run and stamps it on the run's configurable. The remote
runtime sends it as a bearer token on every tool server call, and the tool server
reads the thread and run configuration from the verified claims, never from tool
arguments the model controls.
"""

import time
from typing import Final

import jwt
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from openswe.config import ENV

# The dunder prefix keeps the token out of LangSmith run metadata, which copies every
# other scalar configurable value.
RUNTIME_TOKEN_CONFIG_KEY: Final = "__open_swe_runtime_token__"

_ISSUER: Final = "open-swe"
_AUDIENCE: Final = "open-swe-remote-runtime"
_ALGORITHM: Final = "HS256"
# Longer than a run can live: the platform ends background runs after a day, and an
# enqueued run can wait behind another before it starts.
_TTL_SECONDS: Final = 48 * 60 * 60
_LEEWAY_SECONDS: Final = 60
_REQUIRED_CLAIMS: Final = ("exp", "iat", "iss", "aud", "sub")


class RuntimeTokenError(Exception):
    """A token was refused; the message names the failed check, never the token."""


class RemoteRun(BaseModel):
    """The run a token was signed for, exactly as dispatch stamped it."""

    model_config = ConfigDict(frozen=True)

    thread_id: str
    assistant_id: str
    configurable: dict[str, JsonValue]


def _signing_keys() -> list[str]:
    secret = ENV.REMOTE_RUNTIME_TOKEN_SECRET.optional() or ""
    return [key.strip() for key in secret.split(",") if key.strip()]


def runtime_tokens_configured() -> bool:
    return bool(_signing_keys())


def sign_runtime_token(run: RemoteRun, *, now: int | None = None) -> str:
    keys = _signing_keys()
    if not keys:
        raise RuntimeTokenError("REMOTE_RUNTIME_TOKEN_SECRET is not configured")
    issued_at = int(time.time()) if now is None else now
    claims: dict[str, JsonValue] = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "iat": issued_at,
        "exp": issued_at + _TTL_SECONDS,
        "sub": run.thread_id,
        "assistant_id": run.assistant_id,
        "cfg": run.configurable,
    }
    return jwt.encode(claims, keys[0], algorithm=_ALGORITHM)


def _decode(token: str, keys: list[str]) -> dict[str, object]:
    for key in keys:
        try:
            return jwt.decode(
                token,
                key,
                algorithms=[_ALGORITHM],
                audience=_AUDIENCE,
                issuer=_ISSUER,
                options={"require": list(_REQUIRED_CLAIMS)},
                leeway=_LEEWAY_SECONDS,
            )
        except jwt.InvalidSignatureError:
            continue
        except jwt.PyJWTError as exc:
            raise RuntimeTokenError(type(exc).__name__) from exc
    raise RuntimeTokenError("InvalidSignatureError")


def verify_runtime_token(token: str) -> RemoteRun:
    keys = _signing_keys()
    if not keys:
        raise RuntimeTokenError("REMOTE_RUNTIME_TOKEN_SECRET is not configured")
    claims = _decode(token, keys)
    try:
        return RemoteRun.model_validate(
            {
                "thread_id": claims.get("sub"),
                "assistant_id": claims.get("assistant_id"),
                "configurable": claims.get("cfg"),
            }
        )
    except ValidationError as exc:
        raise RuntimeTokenError("malformed claims") from exc
