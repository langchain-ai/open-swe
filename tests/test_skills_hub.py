from __future__ import annotations

import pytest

from agent.utils import skills_hub
from agent.utils.analyzer_skills import SKILLS_ROUTE
from agent.utils.skills_hub import (
    USER_SKILLS_ROUTE,
    build_agent_skill_routes,
    build_analyzer_skills_backend,
    bundled_skill_files_for_hub,
    global_skills_identifier,
    user_skills_identifier,
)


class _FakeHub:
    def __init__(self, identifier: str, has_commits: bool = False) -> None:
        self.identifier = identifier
        self._has_commits = has_commits

    def has_prior_commits(self) -> bool:
        return self._has_commits


@pytest.fixture(autouse=True)
def _default_repo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSWE_SKILLS_REPO", raising=False)


def test_global_identifier_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    assert global_skills_identifier() == "-/openswe-skills"
    monkeypatch.setenv("OPENSWE_SKILLS_REPO", "acme/skills")
    assert global_skills_identifier() == "acme/skills"


def test_global_identifier_disabled_when_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSWE_SKILLS_REPO", "")
    assert global_skills_identifier() is None
    assert user_skills_identifier("octocat") is None


def test_user_identifier_derives_and_sanitizes(monkeypatch: pytest.MonkeyPatch) -> None:
    assert user_skills_identifier("Octo-Cat") == "-/openswe-skills-octo-cat"
    # Invalid handle chars collapse to hyphens; surrounding hyphens trimmed.
    assert user_skills_identifier("a.b c") == "-/openswe-skills-a-b-c"
    assert user_skills_identifier(None) is None
    assert user_skills_identifier("!!!") is None
    monkeypatch.setenv("OPENSWE_SKILLS_REPO", "acme/skills")
    assert user_skills_identifier("octocat") == "acme/skills-octocat"


def test_build_agent_routes_layers_global_then_user(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[str] = []
    monkeypatch.setattr(
        skills_hub, "_make_hub_backend", lambda ident: made.append(ident) or _FakeHub(ident)
    )
    routes, sources = build_agent_skill_routes("octocat")

    assert set(routes) == {SKILLS_ROUTE, USER_SKILLS_ROUTE}
    assert routes[SKILLS_ROUTE].identifier == "-/openswe-skills"
    assert routes[USER_SKILLS_ROUTE].identifier == "-/openswe-skills-octocat"
    # User source last so it wins on name collisions (SkillsMiddleware is last-wins).
    assert sources == [SKILLS_ROUTE, USER_SKILLS_ROUTE]


def test_build_agent_routes_no_login_global_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(skills_hub, "_make_hub_backend", lambda ident: _FakeHub(ident))
    routes, sources = build_agent_skill_routes(None)
    assert set(routes) == {SKILLS_ROUTE}
    assert sources == [SKILLS_ROUTE]


def test_build_agent_routes_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSWE_SKILLS_REPO", "")
    routes, sources = build_agent_skill_routes("octocat")
    assert routes == {}
    assert sources == []


async def test_analyzer_backend_uses_hub_when_populated(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = _FakeHub("-/openswe-skills", has_commits=True)
    monkeypatch.setattr(skills_hub, "_make_hub_backend", lambda ident: hub)
    backend, sources = await build_analyzer_skills_backend(object())
    assert sources == [SKILLS_ROUTE]
    assert backend.routes[SKILLS_ROUTE] is hub


async def test_analyzer_backend_falls_back_to_bundled_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deepagents.backends.state import StateBackend

    monkeypatch.setattr(
        skills_hub, "_make_hub_backend", lambda ident: _FakeHub(ident, has_commits=False)
    )
    backend, sources = await build_analyzer_skills_backend(object())
    assert sources == [SKILLS_ROUTE]
    assert isinstance(backend.routes[SKILLS_ROUTE], StateBackend)


async def test_analyzer_backend_falls_back_when_hub_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deepagents.backends.state import StateBackend

    monkeypatch.setenv("OPENSWE_SKILLS_REPO", "")
    backend, sources = await build_analyzer_skills_backend(object())
    assert isinstance(backend.routes[SKILLS_ROUTE], StateBackend)


def test_bundled_skill_files_for_hub_paths() -> None:
    files = bundled_skill_files_for_hub()
    assert set(files) == {
        "bootstrap-repo-analysis/SKILL.md",
        "continual-learning/SKILL.md",
    }
    for content in files.values():
        assert content.strip()
