"""Tests for Azure DevOps Service Hook payload parsing."""

from __future__ import annotations

from agent.utils.azure_devops_payload import (
    azure_devops_service_hook_should_process,
    extract_work_item_id_from_payload,
    parse_org_project_from_service_hook,
)
from agent.utils.azure_devops_webhook import verify_azure_devops_webhook_secret


def test_parse_org_project_from_service_hook() -> None:
    payload = {
        "resourceContainers": {
            "project": {
                "baseUrl": "https://dev.azure.com/contoso/MyProject/_apis/projects/x",
            }
        }
    }
    assert parse_org_project_from_service_hook(payload) == ("contoso", "MyProject")


def test_extract_work_item_id() -> None:
    assert extract_work_item_id_from_payload({"resource": {"id": 42}}) == 42


def test_azure_devops_service_hook_should_process() -> None:
    assert not azure_devops_service_hook_should_process({"eventType": "build.complete"})
    assert azure_devops_service_hook_should_process(
        {
            "eventType": "workitem.commented",
            "detailedMessage": {"text": "Please @openswe fix this"},
        }
    )


def test_verify_azure_devops_webhook_secret() -> None:
    from unittest.mock import MagicMock

    req = MagicMock()
    req.headers = {"X-Azure-DevOps-Webhook-Secret": "s3cr3t"}
    assert verify_azure_devops_webhook_secret(req, "s3cr3t")
    assert not verify_azure_devops_webhook_secret(req, "wrong")
