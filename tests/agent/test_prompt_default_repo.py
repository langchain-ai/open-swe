from __future__ import annotations

import asyncio

import pytest

from agent import server


def test_resolve_prompt_default_repo_uses_explicit_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_team_default_repo() -> dict[str, str] | None:
        raise AssertionError("team default should not be loaded")

    monkeypatch.setattr(server, "get_team_default_repo", fake_get_team_default_repo)

    repo = asyncio.run(
        server._resolve_prompt_default_repo({"repo": {"owner": "octo", "name": "repo"}})
    )

    assert repo == {"owner": "octo", "name": "repo"}


def test_resolve_prompt_default_repo_skips_team_default_for_repo_less_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_team_default_repo() -> dict[str, str] | None:
        raise AssertionError("team default should not be loaded")

    monkeypatch.setattr(server, "get_team_default_repo", fake_get_team_default_repo)

    repo = asyncio.run(server._resolve_prompt_default_repo({"repo_explicitly_none": True}))

    assert repo is None


def test_resolve_prompt_default_repo_falls_back_to_team_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_team_default_repo() -> dict[str, str] | None:
        return {"owner": "team", "name": "repo"}

    monkeypatch.setattr(server, "get_team_default_repo", fake_get_team_default_repo)

    repo = asyncio.run(server._resolve_prompt_default_repo({}))

    assert repo == {"owner": "team", "name": "repo"}
