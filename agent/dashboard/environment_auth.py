"""Environment-owned authentication rules for the sandbox HTTP proxy."""

import ipaddress
import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from agent.encryption import decrypt_token, encrypt_token
from agent.store import TypedStore

ENVIRONMENT_AUTH_NAMESPACE = ["environment_auth"]
MAX_AUTH_RULES = 20
MAX_AUTH_REQUEST_BYTES = 200_000

_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_HEADER_RE = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}")
_BLOCKED_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "host",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


class EnvironmentAuthRequestError(ValueError):
    """A validation error whose message is safe to return to the dashboard."""


def _validate_host(value: str) -> str:
    host = value.strip().lower()
    if not host or len(host) > 253 or host.endswith("."):
        raise ValueError("Invalid DNS host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("IP addresses are not allowed")
    if any(not _DNS_LABEL_RE.fullmatch(label) for label in host.split(".")):
        raise ValueError("Invalid DNS host")
    if host == "github.com" or host.endswith(".github.com"):
        raise ValueError("GitHub destinations use managed authentication")
    return host


def _validate_header(value: str) -> str:
    if not _HEADER_RE.fullmatch(value) or value.lower() in _BLOCKED_HEADERS:
        raise ValueError("Invalid authentication header")
    return value


class EnvironmentAuthRule(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")
    host: str = Field(max_length=253)
    header: str = Field(default="Authorization", max_length=128)
    scheme: Literal["bearer", "api_key"]

    _host = field_validator("host")(_validate_host)
    _header = field_validator("header")(_validate_header)


class EnvironmentAuthRuleUpdate(EnvironmentAuthRule):
    credential: SecretStr | None = Field(default=None, min_length=1, max_length=8192, repr=False)

    @field_validator("credential")
    @classmethod
    def _credential(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and any(
            ord(char) < 33 or ord(char) > 126 for char in value.get_secret_value()
        ):
            raise ValueError("Credentials must contain only visible ASCII characters")
        return value


class EnvironmentAuthUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    rules: list[EnvironmentAuthRuleUpdate] = Field(max_length=MAX_AUTH_RULES)

    @model_validator(mode="after")
    def _unique_rules(self) -> EnvironmentAuthUpdate:
        ids: set[str] = set()
        destinations: set[tuple[str, str]] = set()
        for rule in self.rules:
            destination = (rule.host, rule.header.lower())
            if rule.id in ids or destination in destinations:
                raise ValueError("Rule IDs and host/header destinations must be unique")
            ids.add(rule.id)
            destinations.add(destination)
        return self


class _EnvironmentAuthRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    rules: list[EnvironmentAuthRule] = Field(max_length=MAX_AUTH_RULES)
    encrypted_credentials: dict[str, str]

    @model_validator(mode="after")
    def _credentials_match_rules(self) -> _EnvironmentAuthRecord:
        ids = [rule.id for rule in self.rules]
        destinations = [(rule.host, rule.header.lower()) for rule in self.rules]
        if (
            len(ids) != len(set(ids))
            or len(destinations) != len(set(destinations))
            or set(ids) != set(self.encrypted_credentials)
            or any(not value for value in self.encrypted_credentials.values())
        ):
            raise ValueError("Unreadable environment authentication record")
        return self


_store = TypedStore(ENVIRONMENT_AUTH_NAMESPACE, _EnvironmentAuthRecord)


def parse_environment_auth_update(raw_body: bytes) -> EnvironmentAuthUpdate:
    """Parse a request body without allowing Pydantic to echo credential input."""
    if len(raw_body) > MAX_AUTH_REQUEST_BYTES:
        raise EnvironmentAuthRequestError("Environment auth proxy settings are too large")
    try:
        return EnvironmentAuthUpdate.model_validate_json(raw_body)
    except ValidationError:
        raise EnvironmentAuthRequestError("Invalid environment auth proxy settings") from None


def _public(record: _EnvironmentAuthRecord | None) -> dict[str, Any]:
    if record is None:
        return {"rules": []}
    return {
        "rules": [
            {
                **rule.model_dump(mode="json"),
                "has_credential": rule.id in record.encrypted_credentials,
            }
            for rule in record.rules
        ]
    }


def _decrypt_credential(encrypted: str) -> str:
    credential = decrypt_token(encrypted)
    if not credential:
        raise ValueError("Environment authentication credential could not be decrypted")
    return credential


async def get_environment_auth(slug: str) -> dict[str, Any]:
    """Return one environment's non-sensitive rule definitions."""
    return _public(await _store.get(slug))


async def save_environment_auth(slug: str, update: EnvironmentAuthUpdate) -> dict[str, Any]:
    """Atomically replace definitions and encrypted credentials for an environment."""
    previous = await _store.get(slug)
    previous_credentials = previous.encrypted_credentials if previous else {}
    encrypted_credentials: dict[str, str] = {}
    rules: list[EnvironmentAuthRule] = []
    for draft in update.rules:
        credential = draft.credential
        if credential is not None:
            encrypted = encrypt_token(credential.get_secret_value())
        else:
            encrypted = previous_credentials.get(draft.id, "")
            if encrypted:
                _decrypt_credential(encrypted)
        if not encrypted:
            raise EnvironmentAuthRequestError("Every new auth proxy rule requires a credential")
        rules.append(EnvironmentAuthRule.model_validate(draft.model_dump(exclude={"credential"})))
        encrypted_credentials[draft.id] = encrypted

    record = _EnvironmentAuthRecord(
        rules=rules,
        encrypted_credentials=encrypted_credentials,
    )
    await _store.put(slug, record)
    return _public(record)


async def resolve_environment_auth_rules(slug: str) -> list[dict[str, Any]]:
    """Resolve an environment's encrypted values into provider proxy rules."""
    record = await _store.get(slug)
    if record is None:
        return []

    by_host: dict[str, dict[str, Any]] = {}
    for rule in record.rules:
        provider_rule = by_host.setdefault(
            rule.host,
            {
                "name": f"environment-auth-{rule.id}",
                "match_hosts": [rule.host],
                "headers": [],
            },
        )
        credential = _decrypt_credential(record.encrypted_credentials[rule.id])
        value = f"Bearer {credential}" if rule.scheme == "bearer" else credential
        provider_rule["headers"].append({"name": rule.header, "type": "opaque", "value": value})
    return list(by_host.values())


async def delete_environment_auth(slug: str) -> None:
    """Delete all environment-owned authentication definitions and credentials."""
    await _store.delete(slug)
