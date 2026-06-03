from __future__ import annotations

from collections.abc import Iterable

import pytest
from deepagents.backends.protocol import FileDownloadResponse
from deepagents.backends.state import StateBackend

from agent.utils import skills_hub
from agent.utils.analyzer_skills import SKILLS_ROUTE
from agent.utils.skills_hub import (
    USER_SKILLS_ROUTE,
    ReadOnlyBackend,
    _analyzer_playbook_paths,
    build_agent_skill_routes,
    build_analyzer_skills_backend,
    bundled_skill_files_for_hub,
    global_skills_identifier,
    user_skills_identifier,
)


class _FakeHub:
    def __init__(self, identifier: str, present: Iterable[str] = ()) -> None:
        self.identifier = identifier
        self._present = set(present)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [
            FileDownloadResponse(
                path=p,
                content=b"x" if p in self._present else None,
                error=None if p in self._present else "file_not_found",
            )
            for p in paths
        ]


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
    monkeypatch.setattr(skills_hub, "_make_hub_backend", lambda ident: _FakeHub(ident))
    routes, sources = build_agent_skill_routes("octocat")

    assert set(routes) == {SKILLS_ROUTE, USER_SKILLS_ROUTE}
    # Hub routes are wrapped read-only.
    assert isinstance(routes[SKILLS_ROUTE], ReadOnlyBackend)
    assert isinstance(routes[USER_SKILLS_ROUTE], ReadOnlyBackend)
    assert routes[SKILLS_ROUTE].inner.identifier == "-/openswe-skills"
    assert routes[USER_SKILLS_ROUTE].inner.identifier == "-/openswe-skills-octocat"
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


def test_read_only_backend_rejects_writes() -> None:
    backend = ReadOnlyBackend(StateBackend())
    assert backend.write("/skills/x/SKILL.md", "data").error
    assert backend.edit("/skills/x/SKILL.md", "a", "b").error
    [resp] = backend.upload_files([("/skills/x/SKILL.md", b"data")])
    assert resp.error


async def test_analyzer_backend_uses_hub_when_playbooks_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub = _FakeHub("-/openswe-skills", present=_analyzer_playbook_paths())
    monkeypatch.setattr(skills_hub, "_make_hub_backend", lambda ident: hub)
    backend, sources = await build_analyzer_skills_backend(object())
    assert sources == [SKILLS_ROUTE]
    mounted = backend.routes[SKILLS_ROUTE]
    assert isinstance(mounted, ReadOnlyBackend)
    assert mounted.inner is hub


async def test_analyzer_backend_falls_back_when_playbooks_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Repo exists with an unrelated skill but is missing an analyzer playbook.
    present = _analyzer_playbook_paths()[:1]
    monkeypatch.setattr(
        skills_hub, "_make_hub_backend", lambda ident: _FakeHub(ident, present=present)
    )
    backend, _ = await build_analyzer_skills_backend(object())
    assert isinstance(backend.routes[SKILLS_ROUTE].inner, StateBackend)


async def test_analyzer_backend_falls_back_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(skills_hub, "_make_hub_backend", lambda ident: _FakeHub(ident))
    backend, sources = await build_analyzer_skills_backend(object())
    assert sources == [SKILLS_ROUTE]
    assert isinstance(backend.routes[SKILLS_ROUTE].inner, StateBackend)


async def test_analyzer_backend_falls_back_when_hub_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSWE_SKILLS_REPO", "")
    backend, _ = await build_analyzer_skills_backend(object())
    assert isinstance(backend.routes[SKILLS_ROUTE].inner, StateBackend)


def test_bundled_skill_files_for_hub_paths() -> None:
    files = bundled_skill_files_for_hub()
    assert set(files) == {
        "bootstrap-repo-analysis/SKILL.md",
        "continual-learning/SKILL.md",
    }
    for content in files.values():
        assert content.strip()
