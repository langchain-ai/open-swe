"""Tests for SCM pull request abstraction (GitHub implementation)."""

from __future__ import annotations

import pytest

from agent.utils.scm_pull_request import (
    AzureDevOpsPullRequestClient,
    GitHubPullRequestClient,
    pull_request_client_from_repo_config,
)


def test_pull_request_client_github_default() -> None:
    client = pull_request_client_from_repo_config(
        {"owner": "acme", "name": "repo"},
        "github-token",
    )
    assert isinstance(client, GitHubPullRequestClient)


def test_pull_request_client_explicit_github() -> None:
    client = pull_request_client_from_repo_config(
        {"owner": "acme", "name": "repo", "scm_provider": "github"},
        "token",
    )
    assert isinstance(client, GitHubPullRequestClient)


def test_azure_devops_client_organization_key() -> None:
    client = pull_request_client_from_repo_config(
        {
            "scm_provider": "azure_devops",
            "organization": "contoso",
            "project": "Fabrikam",
            "name": "Fabrikam-Fiber-Git",
        },
        "pat-token",
    )
    assert isinstance(client, AzureDevOpsPullRequestClient)


def test_azure_devops_client_owner_key_github_parity() -> None:
    client = pull_request_client_from_repo_config(
        {
            "scm_provider": "azure_devops",
            "owner": "contoso",
            "project": "Fabrikam",
            "name": "Fabrikam-Fiber-Git",
        },
        "pat-token",
    )
    assert isinstance(client, AzureDevOpsPullRequestClient)


def test_unsupported_scm_provider() -> None:
    with pytest.raises(NotImplementedError, match="Unsupported scm_provider"):
        pull_request_client_from_repo_config(
            {"owner": "x", "name": "y", "scm_provider": "gitlab"},
            "token",
        )


def test_missing_owner_or_name() -> None:
    with pytest.raises(ValueError, match="owner"):
        pull_request_client_from_repo_config({"name": "y"}, "token")
    with pytest.raises(ValueError, match="owner"):
        pull_request_client_from_repo_config({"owner": "x"}, "token")


def test_azure_devops_missing_org() -> None:
    with pytest.raises(ValueError, match="owner"):
        pull_request_client_from_repo_config(
            {"scm_provider": "azure_devops", "project": "P", "name": "r"},
            "pat",
        )
