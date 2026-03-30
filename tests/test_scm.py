from __future__ import annotations

from agent.utils.scm import (
    get_clone_url,
    get_git_credential_host_url,
    get_git_credential_username,
    get_review_request_label,
    get_scm_provider,
)


def test_get_scm_provider_defaults_to_github(monkeypatch) -> None:
    monkeypatch.delenv("SCM_PROVIDER", raising=False)
    assert get_scm_provider() == "github"


def test_get_scm_provider_uses_repo_config_first(monkeypatch) -> None:
    monkeypatch.setenv("SCM_PROVIDER", "github")
    assert get_scm_provider({"provider": "gitlab"}) == "gitlab"


def test_get_clone_url_for_gitlab(monkeypatch) -> None:
    monkeypatch.setenv("SCM_PROVIDER", "gitlab")
    monkeypatch.setenv("GITLAB_URL", "http://gitlab.local")
    assert get_clone_url("group/subgroup", "project") == "http://gitlab.local/group/subgroup/project.git"


def test_gitlab_credentials_use_oauth2(monkeypatch) -> None:
    monkeypatch.setenv("SCM_PROVIDER", "gitlab")
    monkeypatch.setenv("GITLAB_URL", "http://gitlab.local")
    assert get_git_credential_username("gitlab") == "oauth2"
    assert get_git_credential_host_url("gitlab") == "http://gitlab.local"
    assert get_review_request_label("gitlab") == "merge request"