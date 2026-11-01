"""Explicit edits use shared evidence without querying GitHub on the edit path."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from runtime_api.edit_conflicts import EditedFile
from sqlalchemy import text

from openswe.database import postgres
from openswe.github.edit_conflicts import EditOverlap, FileChangeEvent
from openswe.github.repositories import Repository
from openswe.run_config import Repo, RunConfig
from openswe.threads import index

pytestmark = pytest.mark.usefixtures("registry_db")


async def test_edit_overlap_is_scoped_and_closed_prs_stop_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await Repository(full_name="acme/api").save()
    monkeypatch.setattr(
        "openswe.github.edit_conflicts.WORKSPACES.get",
        AsyncMock(return_value=SimpleNamespace(repos=["acme/api"])),
    )
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://oswe.invalid")
    monkeypatch.setattr("openswe.github.edit_conflicts.THREADS.get", index.get)
    for thread, visibility in (("first", "public"), ("second", "public"), ("secret", "private")):
        await index.create(
            thread,
            runtime="mda",
            metadata={
                "source": "web",
                "visibility": visibility,
                "workspace": "default",
                "owner_login": "alice",
            },
        )
    first = EditOverlap(RunConfig(thread_id="first", repo=Repo(owner="acme", name="api")))
    second = EditOverlap(RunConfig(thread_id="second", repo=Repo(owner="acme", name="api")))
    secret = EditOverlap(RunConfig(thread_id="secret", repo=Repo(owner="acme", name="api")))
    edit = EditedFile(repository="ACME/API", path="src/app.py", branch="feature")
    assert not (await secret.check(edit)).reminder
    assert not (await first.check(edit)).reminder
    assert "/agents/first" in (await second.check(edit)).reminder
    assert "/agents/secret" not in (await second.check(edit)).reminder
    assert not (
        await second.check(edit.model_copy(update={"repository": "other/private"}))
    ).reminder
    assert not (await second.check(edit.model_copy(update={"path": "../escape"}))).reminder

    files = AsyncMock(return_value=[{"filename": "src/app.py", "previous_filename": "src/old.py"}])
    client = SimpleNamespace(
        repo=lambda *_: SimpleNamespace(pull_request=lambda _: SimpleNamespace(files=files))
    )
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr("openswe.github.edit_conflicts.GitHubClient.as_app", lambda *_: context)
    payload = {
        "action": "opened",
        "repository": {"full_name": "acme/api", "private": True},
        "pull_request": {
            "number": 7,
            "state": "open",
            "head": {"sha": "abc", "ref": "other"},
            "updated_at": "2026-11-01T12:00:00Z",
            "changed_files": 1,
        },
    }
    await FileChangeEvent.receive(payload)
    await FileChangeEvent.receive(payload)
    assert files.await_count == 1
    assert "github.com/acme/api/pull/7" in (await first.check(edit)).reminder
    assert (
        "github.com/acme/api/pull/7"
        in (await first.check(edit.model_copy(update={"path": "src/old.py"}))).reminder
    )
    assert (
        "github.com/acme/api/pull/7"
        not in (await first.check(edit.model_copy(update={"branch": "other"}))).reminder
    )
    payload["action"] = "closed"
    payload["pull_request"]["updated_at"] = "2026-11-01T13:00:00Z"
    await FileChangeEvent.receive(payload)
    assert "github.com/acme/api/pull/7" not in (await first.check(edit)).reminder
    payload["action"] = "synchronize"
    payload["pull_request"]["updated_at"] = "2026-11-01T12:30:00Z"
    await FileChangeEvent.receive(payload)
    assert "github.com/acme/api/pull/7" not in (await first.check(edit)).reminder
    assert files.await_count == 1
    payload["action"] = "reopened"
    payload["pull_request"]["updated_at"] = "2026-11-01T14:00:00Z"
    await FileChangeEvent.receive(payload)
    assert files.await_count == 2
    assert "github.com/acme/api/pull/7" in (await first.check(edit)).reminder
    files.return_value = [{"filename": "different.py"}]
    payload["action"] = "synchronize"
    payload["pull_request"]["head"]["sha"] = "def"
    payload["pull_request"]["updated_at"] = "2026-11-01T15:00:00Z"
    await FileChangeEvent.receive(payload)
    assert files.await_count == 3
    assert "github.com/acme/api/pull/7" not in (await first.check(edit)).reminder
    async with postgres.session() as session:
        await session.execute(
            text(
                "UPDATE session_file_edit SET updated_at = clock_timestamp() - interval '25 hours'"
            )
        )
    assert not (await first.check(edit)).reminder
