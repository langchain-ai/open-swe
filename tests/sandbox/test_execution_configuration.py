import pytest
from pydantic import JsonValue

from openswe.dashboard.execution_defaults import ExecutionDefaults


@pytest.mark.parametrize(
    "create_params",
    [
        {"env_vars": {"API_TOKEN": "sensitive"}},
        {"env_vars": {"OPENAI_API_KEY": "sensitive"}},
        {"clientSecret": "sensitive"},
        {
            "proxy_config": {
                "rules": [
                    {"headers": [{"name": "Authorization", "type": "opaque", "value": "sensitive"}]}
                ]
            }
        },
        {
            "proxy_config": {
                "rules": [{"headers": [{"name": "X-OpenAI-Api-Key", "value": "sensitive"}]}]
            }
        },
    ],
)
def test_create_params_reject_persisted_secrets(create_params: dict[str, JsonValue]) -> None:
    with pytest.raises(ValueError, match="must not contain secrets"):
        ExecutionDefaults(create_params=create_params)


def test_create_params_enforce_serialized_size_limit() -> None:
    with pytest.raises(ValueError, match="at most"):
        ExecutionDefaults(
            create_params={"metadata": "x" * 20_000},
        )


_TAG = "00000000-0000-0000-0000-000000000001"
_AWS_RULE: dict[str, JsonValue] = {
    "name": "aws",
    "type": "aws",
    "enabled": True,
    "aws": {"role_arn": "arn:aws:iam::123456789012:role/ReadOnly", "subject_scope": f"tag:{_TAG}"},
}


@pytest.mark.parametrize(
    "params",
    [
        {"proxy_config": {"rules": [_AWS_RULE]}},
        {"tag_value_ids": ["not-a-uuid"]},
        {"tag_value_ids": [_TAG], "proxy_config": {"rules": [_AWS_RULE, _AWS_RULE]}},
        {"tag_value_ids": [_TAG], "proxy_config": {"rules": [{**_AWS_RULE, "enabled": False}]}},
        {"tag_value_ids": [_TAG], "proxy_config": {"rules": [{**_AWS_RULE, "name": "github"}]}},
        {
            "tag_value_ids": [_TAG],
            "proxy_config": {
                "rules": [
                    {
                        **_AWS_RULE,
                        "aws": {
                            "role_arn": "arn:aws:iam::123456789012:role/ReadOnly",
                            "subject_scope": "workspace",
                        },
                    }
                ]
            },
        },
        {
            "proxy_config": {
                "rules": [
                    {**_AWS_RULE, "aws": {"role_arn": "arn:aws:iam::123456789012:role/ReadOnly"}}
                ]
            }
        },
        {
            "mount_config": {
                "auth": {"aws": {"role_arn": "arn:aws:iam::123456789012:role/ReadOnly"}},
                "mounts": [],
            }
        },
        {
            "proxy_config": {
                "rules": [
                    {
                        "name": "azure",
                        "type": "azure",
                        "azure": {"tenant_id": "domain.example", "client_id": _TAG},
                    }
                ]
            }
        },
        {"access_delegation": {"mode": "INHERIT"}},
        {"access_delegation": {"mode": "EXPLICIT", "permissions": []}},
        {"access_delegation": {"mode": "EXPLICIT", "permissions": ["workspaces:manage"]}},
    ],
)
def test_cloud_authority_rejects_invalid_or_unassigned_scopes(params: dict[str, JsonValue]) -> None:
    with pytest.raises(ValueError):
        ExecutionDefaults(create_params=params)
    with pytest.raises(ValueError):
        ExecutionDefaults.model_construct(create_params=params).sandbox_create_params()


def test_cloud_authority_round_trips_without_changing_federated_rules() -> None:
    params: dict[str, JsonValue] = {
        "tag_value_ids": [_TAG],
        "proxy_config": {
            "rules": [
                _AWS_RULE,
                {
                    "name": "gcp",
                    "type": "gcp",
                    "gcp": {
                        "workload_identity_provider": "//iam.googleapis.com/projects/123/locations/global/workloadIdentityPools/pool/providers/provider",
                        "subject_scope": f"tag:{_TAG}",
                    },
                },
                {
                    "name": "azure",
                    "type": "azure",
                    "azure": {
                        "tenant_id": _TAG,
                        "client_id": _TAG,
                        "resources": ["https://storage.azure.com"],
                        "subject_scope": f"tag:{_TAG}",
                    },
                },
            ]
        },
        "access_delegation": {
            "mode": "EXPLICIT",
            "permissions": ["sandboxes:create", "projects:read"],
        },
    }
    assert ExecutionDefaults(create_params=params).create_params == params
    assert ExecutionDefaults(create_params=params).sandbox_create_params() == params


def test_static_cloud_rules_keep_their_existing_json_contract() -> None:
    params: dict[str, JsonValue] = {
        "proxy_config": {
            "enabled": False,
            "rules": [
                {
                    "name": "existing-gcp",
                    "type": "gcp",
                    "enabled": False,
                    "gcp": {
                        "service_account_json": {
                            "type": "workspace_secret",
                            "value": "{GCP_SERVICE_ACCOUNT_JSON}",
                        }
                    },
                }
            ],
        },
    }
    assert ExecutionDefaults(create_params=params).create_params == params
    assert ExecutionDefaults.model_construct(create_params=params).sandbox_create_params() == params


def test_historical_toggle_is_ignored_and_not_exposed() -> None:
    defaults = ExecutionDefaults.model_validate({"enabled": False, "snapshot_id": "global-image"})
    assert defaults.snapshot_id == "global-image"
    assert "enabled" not in defaults.model_dump()
    assert "enabled" not in ExecutionDefaults.model_json_schema()["properties"]


def test_only_historical_global_queue_targets_are_accepted() -> None:
    from openswe.execution_images import ExecutionTarget

    target = ExecutionTarget.model_validate({"kind": "global", "workspace_slug": None})
    assert target.model_dump(mode="json") == {"kind": "global"}
    with pytest.raises(ValueError):
        ExecutionTarget.model_validate({"kind": "global", "workspace_slug": "forged"})
    with pytest.raises(ValueError):
        ExecutionTarget.model_validate({"kind": "workspace", "workspace_slug": "deleted"})
