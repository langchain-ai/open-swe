"""Validate nonsecret sandbox federation settings before persisting or using them."""

import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


class CloudIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_scope: str = "workspace"

    @field_validator("subject_scope")
    @classmethod
    def validate_scope(cls, value: str) -> str:
        if value != "workspace":
            if not value.startswith("tag:"):
                raise ValueError("subject_scope must be workspace or tag:<tag value UUID>")
            UUID(value.removeprefix("tag:"))
        return value


class AWSIdentity(CloudIdentity):
    role_arn: str = Field(pattern=r"^arn:aws:iam::[0-9]{12}:role/[^\s]+$")


class GCPIdentity(CloudIdentity):
    workload_identity_provider: str = Field(
        pattern=r"^//iam\.googleapis\.com/projects/[0-9]+/locations/global/workloadIdentityPools/[^/\s]+/providers/[^/\s]+$"
    )
    service_account_email: str | None = Field(
        default=None, pattern=r"^[^\s@]+@[^\s@]+\.iam\.gserviceaccount\.com$"
    )
    scopes: list[str] | None = None


class AzureIdentity(CloudIdentity):
    tenant_id: UUID
    client_id: UUID
    resources: (
        list[
            Literal[
                "https://storage.azure.com",
                "https://vault.azure.net",
                "https://management.azure.com",
                "https://cognitiveservices.azure.com",
            ]
        ]
        | None
    ) = Field(default=None, min_length=1)


_CLOUDS = {"aws": AWSIdentity, "gcp": GCPIdentity, "azure": AzureIdentity}
_MANAGED_NAMES = {
    "github",
    "github-api",
    "github-public",
    "open-swe-langsmith",
    "stagehand-model",
    "open-swe-thread-tools",
}


def validate_cloud_identity(params: dict[str, JsonValue]) -> None:
    tags = params.get("tag_value_ids", [])
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise ValueError("tag_value_ids must be an array of governed LangSmith tag value UUIDs")
    tag_ids = {str(UUID(str(tag))) for tag in tags}
    proxy = params.get("proxy_config")
    if not isinstance(proxy, dict):
        return
    rules = proxy.get("rules", [])
    if not isinstance(rules, list):
        return
    seen: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        cloud = rule.get("type")
        if not isinstance(cloud, str) or cloud not in _CLOUDS:
            continue
        if cloud in seen:
            raise ValueError(f"at most one {cloud} rule is allowed per sandbox")
        seen.add(cloud)
        settings = rule.get(cloud)
        federation_field = {
            "aws": "role_arn",
            "gcp": "workload_identity_provider",
            "azure": "tenant_id",
        }[cloud]
        if not isinstance(settings, dict) or federation_field not in settings:
            continue
        if rule.get("enabled", True) is not True or proxy.get("enabled", True) is not True:
            raise ValueError("cloud identity rules must stay enabled")
        name = rule.get("name")
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[\w.-]+", name)
            or name in _MANAGED_NAMES
        ):
            raise ValueError("cloud identity rules need a valid, non-managed rule name")
        identity = _CLOUDS[cloud].model_validate(rule.get(cloud))
        if identity.subject_scope.startswith("tag:"):
            scope_tag = str(UUID(identity.subject_scope.removeprefix("tag:")))
            if scope_tag not in tag_ids:
                raise ValueError("a tag-scoped cloud identity must be included in tag_value_ids")
