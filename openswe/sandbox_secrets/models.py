"""Personal sandbox secrets: credentials the sandbox auth proxy injects for their owner.

A secret never enters the sandbox. Its environment variable holds a placeholder so
tools that insist on one start, and the proxy writes the real value into a header of
requests to the secret's hosts. Only sandboxes of their owner's private threads get
them (see ``openswe.sandbox_secrets.proxy``).
"""

import re
from datetime import datetime
from typing import Self
from uuid import UUID, uuid7

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import ForeignKey, delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.encryption import decrypt_token, encrypt_token
from openswe.users.models import User

PLACEHOLDER = "open-swe-proxy-injected"
RULE_PREFIX = "user-secret-"
VALUE_TOKEN = "{value}"
MAX_SECRETS = 50
MAX_HOSTS = 10
MAX_VALUE_CHARS = 16_384

_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")
_HOST = re.compile(r"(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]")
_HEADER = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,128}")
# Set by Open SWE's own proxy rules, the shell, or the proxy's TLS and AWS/GCP handling.
_RESERVED_NAMES = frozenset(
    {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "OPEN_SWE_TOOLS_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "HOME",
        "PATH",
        "PWD",
        "SHELL",
        "USER",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
    }
)
_RESERVED_PREFIXES = ("AWS_", "CLOUDSDK_", "LD_")
_RESERVED_HEADERS = frozenset(
    {"host", "content-length", "transfer-encoding", "connection", "upgrade", "te", "trailer"}
)


class SecretRejected(ValueError):
    """A secret the person must change before it can be saved."""


class SecretSettings(BaseModel):
    """Where a secret is sent, without its value."""

    hosts: list[str] = Field(min_length=1, max_length=MAX_HOSTS)
    header_name: str = "Authorization"
    header_format: str = f"Bearer {VALUE_TOKEN}"

    @field_validator("hosts")
    @classmethod
    def _hosts(cls, hosts: list[str]) -> list[str]:
        cleaned = sorted({host.strip().lower() for host in hosts if host.strip()})
        if not cleaned:
            raise ValueError("at least one host is required")
        for host in cleaned:
            if not _HOST.fullmatch(host) or len(host) > 253:
                raise ValueError(
                    f"{host!r} is not a hostname; use a bare host such as api.example.com "
                    "or *.example.com, without a scheme, path or port"
                )
            if host.startswith("*.") and host.count(".") < 2:
                raise ValueError(f"{host!r} must wildcard a registrable domain")
            bare = host.removeprefix("*.")
            if bare == "github.com" or bare.endswith(".github.com"):
                raise ValueError("GitHub hosts are authenticated by Open SWE itself")
        return cleaned

    @field_validator("header_name")
    @classmethod
    def _header_name(cls, name: str) -> str:
        name = name.strip()
        if not _HEADER.fullmatch(name) or name.lower() in _RESERVED_HEADERS:
            raise ValueError(f"{name!r} is not a header the proxy can set")
        return name

    @field_validator("header_format")
    @classmethod
    def _header_format(cls, template: str) -> str:
        if template.count(VALUE_TOKEN) != 1:
            raise ValueError(f"the header format must contain {VALUE_TOKEN} exactly once")
        if any(char in template for char in "\r\n") or len(template) > 256:
            raise ValueError("the header format must be a single short line")
        return template


def validate_name(name: str) -> str:
    """The environment variable a secret is announced under, or SecretRejected."""
    if not _NAME.fullmatch(name):
        raise SecretRejected("Secret names are environment variable names: A-Z, 0-9 and _")
    if name in _RESERVED_NAMES or name.startswith(_RESERVED_PREFIXES):
        raise SecretRejected(f"{name} is reserved by the sandbox")
    return name


def validate_value(value: str) -> str:
    value = value.strip()
    if not value:
        raise SecretRejected("A secret needs a value")
    if len(value) > MAX_VALUE_CHARS or any(char in value for char in "\r\n\0"):
        raise SecretRejected("A secret value must be one line of at most 16,384 characters")
    return value


class SecretView(SecretSettings):
    """A stored secret as the dashboard and agent see it. Has no field for the value."""

    name: str
    updated_at: datetime | None = None


class SandboxSecret(Base):
    __tablename__ = "user_sandbox_secret"

    user_id: Mapped[UUID] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"))
    name: Mapped[str]
    encrypted_value: Mapped[str]
    hosts: Mapped[list[str]] = mapped_column(JSONB)
    header_name: Mapped[str]
    header_format: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )

    @classmethod
    async def owned_by(cls, user_id: UUID) -> list[Self]:
        async with postgres.session() as session:
            return list(
                await session.scalars(select(cls).where(cls.user_id == user_id).order_by(cls.name))
            )

    @classmethod
    async def save(
        cls, user_id: UUID, name: str, settings: SecretSettings, value: str | None
    ) -> None:
        """Create or update the owner's secret; an update without a value keeps the stored one."""
        name = validate_name(name)
        columns: dict[str, object] = settings.model_dump()
        if value is not None:
            columns["encrypted_value"] = encrypt_token(validate_value(value))
        async with postgres.session() as session:
            existing = await session.scalar(
                select(cls.id).where(cls.user_id == user_id, cls.name == name)
            )
            if existing is not None:
                await session.execute(
                    update(cls)
                    .where(cls.id == existing)
                    .values(**columns, updated_by=user_id, updated_at=NOW)
                )
                return
            if value is None:
                raise SecretRejected("A new secret needs a value")
            count = await session.scalar(
                select(func.count()).select_from(cls).where(cls.user_id == user_id)
            )
            if (count or 0) >= MAX_SECRETS:
                raise SecretRejected(f"You can keep at most {MAX_SECRETS} sandbox secrets")
            await session.execute(
                insert(cls)
                .values(
                    id=uuid7(),
                    user_id=user_id,
                    name=name,
                    created_by=user_id,
                    updated_by=user_id,
                    **columns,
                )
                .on_conflict_do_nothing(index_elements=[cls.user_id, cls.name])
            )

    @classmethod
    async def remove(cls, user_id: UUID, name: str) -> bool:
        async with postgres.session() as session:
            removed = await session.scalar(
                delete(cls).where(cls.user_id == user_id, cls.name == name).returning(cls.id)
            )
        return removed is not None

    def view(self) -> SecretView:
        return SecretView(
            name=self.name,
            hosts=self.hosts,
            header_name=self.header_name,
            header_format=self.header_format,
            updated_at=self.updated_at,
        )

    def proxy_rule(self) -> dict[str, object] | None:
        """The auth proxy rule injecting this secret, or None when it cannot be decrypted."""
        value = decrypt_token(self.encrypted_value)
        if not value:
            return None
        return {
            "name": f"{RULE_PREFIX}{self.name.lower().replace('_', '-')}",
            "match_hosts": list(self.hosts),
            "headers": [
                {
                    "name": self.header_name,
                    "type": "opaque",
                    "value": self.header_format.replace(VALUE_TOKEN, value),
                }
            ],
            "env_vars": {self.name: PLACEHOLDER},
        }
