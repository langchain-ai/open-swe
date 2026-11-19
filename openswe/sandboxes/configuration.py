"""Validated nonsecret configuration for Robin's sandbox images."""

import json
import re
from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from openswe.github.repo_names import normalize_repo_full_name
from openswe.sandboxes.cloud_identity import validate_access_delegation, validate_cloud_identity


class SandboxResources(TypedDict, total=False):
    mem_bytes: int
    vcpus: int
    fs_capacity_bytes: int


class ExecutionConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = ""
    repos: list[str] = Field(default_factory=list)
    setup_script: str = ""
    update_script: str = ""
    snapshot_id: str | None = None
    snapshot_name: str | None = None
    base_snapshot_id: str | None = None
    vcpus: int | None = Field(default=None, gt=0)
    mem_bytes: int | None = Field(default=None, gt=0)
    fs_capacity_bytes: int | None = Field(default=None, gt=0)
    create_params: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("prompt", "setup_script", "update_script")
    @classmethod
    def validate_text(cls, value: str) -> str:
        text = value.strip()
        if len(text) > 20_000:
            raise ValueError("instructions and scripts must be at most 20000 characters")
        return text

    @field_validator("snapshot_id", "base_snapshot_id")
    @classmethod
    def normalize_snapshot(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @field_validator("snapshot_name")
    @classmethod
    def validate_snapshot_name(cls, value: str | None) -> str | None:
        text = (value or "").strip()
        if not text:
            return None
        if ":" in text:
            raise ValueError("snapshot_name must not contain a colon")
        if len(text) > 128:
            raise ValueError("snapshot_name must be at most 128 characters")
        return text

    @field_validator("repos")
    @classmethod
    def validate_repos(cls, value: list[str]) -> list[str]:
        if len(value) > 50:
            raise ValueError("at most 50 repositories may be preloaded")
        deduped: dict[str, str] = {}
        for entry in value:
            full_name = normalize_repo_full_name(entry)
            deduped.setdefault(full_name.lower(), full_name)
        return list(deduped.values())

    @staticmethod
    def normalize_parameter_name(value: str) -> str:
        snake_value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value.strip())
        return re.sub(r"[^a-z0-9]+", "_", snake_value.lower()).strip("_")

    @classmethod
    def sensitive_parameter_name(cls, value: str) -> bool:
        normalized = cls.normalize_parameter_name(value)
        return (
            normalized in _SENSITIVE_KEYS
            or normalized.endswith(_SENSITIVE_SUFFIXES)
            or normalized.startswith(_SENSITIVE_PREFIXES)
        )

    @classmethod
    def has_sensitive_parameter(cls, value: JsonValue) -> bool:
        if isinstance(value, dict):
            header_name = value.get("name")
            if isinstance(header_name, str):
                normalized_header = cls.normalize_parameter_name(header_name)
                if normalized_header in _SENSITIVE_HEADERS or cls.sensitive_parameter_name(
                    normalized_header
                ):
                    return True
            for key, nested in value.items():
                if cls.sensitive_parameter_name(key) or cls.has_sensitive_parameter(nested):
                    return True
        elif isinstance(value, list):
            return any(cls.has_sensitive_parameter(item) for item in value)
        return False

    @field_validator("create_params")
    @classmethod
    def validate_create_params(cls, params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        proxy_config = params.get("proxy_config")
        if proxy_config is not None:
            if not isinstance(proxy_config, dict):
                raise ValueError("create_params.proxy_config must be a JSON object")
            if "rules" in proxy_config and not isinstance(proxy_config["rules"], list):
                raise ValueError("create_params.proxy_config.rules must be a JSON array")
        try:
            serialized = json.dumps(params, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("create_params must contain only valid JSON values") from exc
        if len(serialized) > 20_000:
            raise ValueError("create_params must be at most 20000 JSON characters")
        if cls.has_sensitive_parameter(params):
            raise ValueError("create_params must not contain secrets or authentication credentials")
        validate_cloud_identity(params)
        validate_access_delegation(params)
        return params

    def sandbox_resources(self) -> SandboxResources:
        resources: SandboxResources = {}
        if self.vcpus is not None:
            resources["vcpus"] = self.vcpus
        if self.mem_bytes is not None:
            resources["mem_bytes"] = self.mem_bytes
        if self.fs_capacity_bytes is not None:
            resources["fs_capacity_bytes"] = self.fs_capacity_bytes
        return resources

    def sandbox_create_params(self) -> dict[str, JsonValue]:
        return self.validate_create_params(self.create_params)


_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "password",
        "pat",
        "private_key",
        "secret",
        "token",
    }
)
_SENSITIVE_SUFFIXES = (
    "_api_key",
    "_apikey",
    "_authorization",
    "_cookie",
    "_credential",
    "_credentials",
    "_password",
    "_pat",
    "_private_key",
    "_secret",
    "_token",
)
_SENSITIVE_PREFIXES = (
    "api_key_",
    "authorization_",
    "credential_",
    "credentials_",
    "password_",
    "private_key_",
    "secret_",
    "token_",
)
_SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "proxy_authorization", "x_api_key"})
