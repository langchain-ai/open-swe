"""Validate nonsecret sandbox identity settings (cloud federation, LangSmith delegation)."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

_GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


class CloudIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Every Open SWE workspace shares one LangSmith workspace, so a `workspace` subject
    # would let any of them assume the role; only a governed tag separates them.
    subject_scope: str = Field(pattern=rf"^tag:{_GUID}$")


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
    tenant_id: str = Field(pattern=rf"^{_GUID}$")
    client_id: str = Field(pattern=rf"^{_GUID}$")
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


# LangSmith never delegates tenant administration; refusing it here fails at save, not boot.
_UNDELEGATABLE = ("workspaces:manage", "organization:manage", "organization:pats:")


class AccessDelegation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The delegator is Open SWE's own sandbox-creating key, not the person running the
    # thread, so a workspace grants a fixed list rather than everything that key holds.
    mode: Literal["EXPLICIT"]
    permissions: list[str] = Field(min_length=1)

    @field_validator("permissions")
    @classmethod
    def _check_permissions(cls, permissions: list[str]) -> list[str]:
        for permission in permissions:
            if not re.fullmatch(r"[a-z0-9_-]+(:[a-z0-9_-]+)+", permission):
                raise ValueError(f"invalid LangSmith permission {permission!r}")
            if permission.startswith(_UNDELEGATABLE):
                raise ValueError(f"LangSmith does not delegate {permission!r}")
        return permissions


_CLOUDS = {"aws": AWSIdentity, "gcp": GCPIdentity, "azure": AzureIdentity}
_FEDERATION_FIELDS = {
    "aws": {"role_arn"},
    "gcp": {"workload_identity_provider"},
    "azure": {"tenant_id", "client_id"},
}
_MANAGED_NAMES = {
    "github",
    "github-api",
    "github-public",
    "open-swe-langsmith",
    "stagehand-model",
    "open-swe-thread-tools",
}


def federated_cloud(rule: object) -> str | None:
    """The cloud a proxy rule federates with; LangSmith fixes these rules at sandbox creation."""
    if not isinstance(rule, dict):
        return None
    cloud = rule.get("type")
    if not isinstance(cloud, str) or cloud not in _CLOUDS:
        return None
    settings = rule.get(cloud)
    if not isinstance(settings, dict) or not _FEDERATION_FIELDS[cloud] & settings.keys():
        return None
    return cloud


def _validate_federated(cloud: str, settings: object, tag_ids: set[str]) -> bool:
    """Validate ``settings`` when they federate; whether they do."""
    if not isinstance(settings, dict) or not _FEDERATION_FIELDS[cloud] & settings.keys():
        return False
    identity = _CLOUDS[cloud].model_validate(settings)
    if identity.subject_scope.removeprefix("tag:").lower() not in tag_ids:
        raise ValueError("a tag-scoped cloud identity must be included in tag_value_ids")
    return True


def validate_cloud_identity(params: dict[str, JsonValue]) -> None:
    tags = params.get("tag_value_ids", [])
    if not isinstance(tags, list) or not all(
        isinstance(tag, str) and re.fullmatch(_GUID, tag) for tag in tags
    ):
        raise ValueError("tag_value_ids must be an array of governed LangSmith tag value UUIDs")
    tag_ids = {str(tag).lower() for tag in tags}
    mounts = params.get("mount_config")
    auth = mounts.get("auth") if isinstance(mounts, dict) else None
    if isinstance(auth, dict):
        for cloud in _CLOUDS:
            _validate_federated(cloud, auth.get(cloud), tag_ids)
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
        if not _validate_federated(cloud, rule.get(cloud), tag_ids):
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


def validate_access_delegation(params: dict[str, JsonValue]) -> None:
    grant = params.get("access_delegation")
    if grant is not None:
        AccessDelegation.model_validate(grant)
