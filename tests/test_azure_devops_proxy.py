"""Tests for Azure DevOps LangSmith proxy rules."""

from __future__ import annotations

import base64

from agent.integrations.langsmith import _azure_devops_proxy_rules


def test_azure_devops_proxy_rules_basic_auth() -> None:
    rules = _azure_devops_proxy_rules("my-secret-pat")
    assert len(rules) == 1
    rule = rules[0]
    assert "dev.azure.com" in rule["match_hosts"]
    header = rule["headers"][0]
    assert header["name"] == "Authorization"
    expected = base64.b64encode(b":my-secret-pat").decode()
    assert header["value"] == f"Basic {expected}"
