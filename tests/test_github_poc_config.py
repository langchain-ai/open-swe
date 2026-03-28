from __future__ import annotations

import importlib

from agent import webapp
from agent.utils import github_user_email_map


def test_repo_allowlist_accepts_matching_repo(monkeypatch) -> None:
    monkeypatch.setattr(webapp, "ALLOWED_GITHUB_ORGS", frozenset({"langchain-ai"}))
    monkeypatch.setattr(webapp, "ALLOWED_GITHUB_REPOS", frozenset({"langchain-ai/open-swe"}))

    assert webapp._is_repo_allowed({"owner": "langchain-ai", "name": "open-swe"}) is True


def test_repo_allowlist_rejects_non_matching_repo(monkeypatch) -> None:
    monkeypatch.setattr(webapp, "ALLOWED_GITHUB_ORGS", frozenset({"langchain-ai"}))
    monkeypatch.setattr(webapp, "ALLOWED_GITHUB_REPOS", frozenset({"langchain-ai/open-swe"}))

    assert webapp._is_repo_allowed({"owner": "langchain-ai", "name": "langgraph"}) is False


def test_github_user_email_map_json_overrides_defaults(monkeypatch) -> None:
    monkeypatch.setenv(
        "GITHUB_USER_EMAIL_MAP_JSON",
        '{"octocat":"octocat@example.com","hwchase17":"override@example.com"}',
    )

    reloaded = importlib.reload(github_user_email_map)

    assert reloaded.GITHUB_USER_EMAIL_MAP["octocat"] == "octocat@example.com"
    assert reloaded.GITHUB_USER_EMAIL_MAP["hwchase17"] == "override@example.com"
