"""Run tokens: the identity a remotely hosted run carries back to this backend.

Dispatch signs one per run and stamps it, with the run's configuration, on the
run's context. The remote runtime sends the token as a bearer token on every tool
server call and echoes the configuration in the body. The token signs only a hash
of that configuration, so it stays small enough for a header whatever the run
carries, and the tool server accepts the configuration only when it matches.
Nothing the model controls can change either.
"""

import hashlib
import hmac
import json
import time
from typing import Final

import jwt
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError

from openswe.config import ENV

_ISSUER: Final = "open-swe"
_AUDIENCE: Final = "open-swe-remote-runtime"
_ALGORITHM: Final = "HS256"
# Longer than a run can live: the platform ends background runs after a day, and an
# enqueued run can wait behind another before it starts.
_TTL_SECONDS: Final = 48 * 60 * 60
_LEEWAY_SECONDS: Final = 60
_REQUIRED_CLAIMS: Final = ("exp", "iat", "iss", "aud", "sub")
_configurable = TypeAdapter(dict[str, JsonValue])


class RuntimeTokenError(Exception):
    """A token was refused; the message names the failed check, never the token."""


class RemoteRun(BaseModel):
    """The run a token was signed for, exactly as dispatch stamped it."""

    model_config = ConfigDict(frozen=True)

    thread_id: str
    assistant_id: str
    configurable: dict[str, JsonValue]

    def config_text(self) -> str:
        """The configuration as the exact text the token's hash covers.

        The runtime echoes this text unchanged, so no JSON round trip on the way
        (a number turning into a float, say) can break the match.
        """
        return json.dumps(self.configurable, sort_keys=True, separators=(",", ":"))


class RunTicket(BaseModel):
    """What a verified token proves: the run, short of the configuration it was signed with."""

    model_config = ConfigDict(frozen=True)

    thread_id: str
    assistant_id: str
    config_sha256: str | None = None
    # Tokens signed before the hash carried the configuration itself. Accepted until
    # they expire, so runs dispatched before the change keep working.
    configurable: dict[str, JsonValue] | None = None

    def run(self, config_text: str | None) -> RemoteRun:
        """The run, with ``config_text`` as its configuration if the token signed it."""
        if self.configurable is not None:
            configurable = self.configurable
        elif (
            config_text is None
            or self.config_sha256 is None
            or not hmac.compare_digest(_sha256(config_text), self.config_sha256)
        ):
            raise RuntimeTokenError("run configuration does not match the token")
        else:
            try:
                configurable = _configurable.validate_json(config_text)
            except ValidationError as exc:
                raise RuntimeTokenError("malformed run configuration") from exc
        return RemoteRun(
            thread_id=self.thread_id, assistant_id=self.assistant_id, configurable=configurable
        )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


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
        "cfg_sha256": _sha256(run.config_text()),
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


def verify_runtime_token(token: str) -> RunTicket:
    keys = _signing_keys()
    if not keys:
        raise RuntimeTokenError("REMOTE_RUNTIME_TOKEN_SECRET is not configured")
    claims = _decode(token, keys)
    if claims.get("cfg_sha256") is None and claims.get("cfg") is None:
        raise RuntimeTokenError("malformed claims")
    try:
        return RunTicket.model_validate(
            {
                "thread_id": claims.get("sub"),
                "assistant_id": claims.get("assistant_id"),
                "config_sha256": claims.get("cfg_sha256"),
                "configurable": claims.get("cfg"),
            }
        )
    except ValidationError as exc:
        raise RuntimeTokenError("malformed claims") from exc
