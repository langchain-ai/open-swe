from importlib import import_module
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from openswe.database import transaction
from openswe.github.pull_requests import PullRequest
from openswe.run_config import RunConfig
from openswe.tasks.store import Task
from openswe.threads import index
from openswe.tools import suggest_task as tool
from openswe.users import User
from openswe.workspaces.store import WORKSPACES, WorkspaceCreate


@pytest.mark.usefixtures("registry_db")
class TestSuggestedTasks:
    @pytest.mark.parametrize("source", ["dashboard", "github"])
    async def test_cross_workspace_suggestion_keeps_origin_and_publishes_event(
        self, monkeypatch: pytest.MonkeyPatch, source: str
    ) -> None:
        await WORKSPACES.create(WorkspaceCreate(name="Origin", repos=["acme/origin"]), "alice")
        await WORKSPACES.create(WorkspaceCreate(name="Target", repos=["acme/target"]), "alice")
        await index.create("origin-thread", runtime="mda")
        monkeypatch.setattr(
            "openswe.users.models.is_authorized_github_login", AsyncMock(return_value=True)
        )
        user = await User.sign_in("github", "123", login="alice")
        task = await Task.for_thread("origin-thread", title="Original task", workspace="origin")
        pull = await (await PullRequest.load("acme", "origin", 7)).ensure()
        monkeypatch.setattr(
            RunConfig,
            "from_runtime",
            lambda: RunConfig.parse(
                {
                    "thread_id": "origin-thread",
                    "workspace": "origin",
                    "source": source,
                    "github_login": "alice",
                    "user_id": str(user.id),
                    "pr_url": "https://github.com/acme/origin/pull/7",
                }
            ),
        )
        monkeypatch.setattr(
            import_module("openswe.tools.suggest_task"), "require_repo_access_for_user", AsyncMock()
        )
        result = await tool(
            summary="Fix old bug",
            details="A pre-existing bug outside the original task.",
            repo="acme/target",
            workspace="target",
            context_links=["https://github.com/acme/target/issues/9"],
        )
        assert result["success"] is True
        async with transaction() as conn:
            row = (
                (
                    await conn.execute(
                        text("SELECT * FROM suggested_task WHERE id = :id"),
                        {"id": UUID(str(result["suggested_task_id"]))},
                    )
                )
                .mappings()
                .one()
            )
            event = (
                (
                    await conn.execute(
                        text("SELECT * FROM event_log WHERE event_type = 'task.suggested'")
                    )
                )
                .mappings()
                .one()
            )
        assert row["workspace_id"] == await WORKSPACES.id_for_slug("target")
        assert row["thread_id"] == "origin-thread"
        assert row["task_id"] == task.id
        assert row["pull_request_id"] == pull.id
        assert row["triggered_by"] == user.id
        assert (
            row["created_by"] == row["updated_by"] == (user.id if source == "dashboard" else None)
        )
        assert row["id"].version == 7
        assert row["created_at"] is not None and row["updated_at"] is not None
        assert event["workspace_id"] == row["workspace_id"]
        assert event["repository_id"] == row["repository_id"]
        assert event["payload"]["context_links"] == row["context_links"]
        assert event["payload"]["id"] == str(row["id"])

    async def test_denied_target_does_not_record_a_suggestion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await WORKSPACES.create(WorkspaceCreate(name="Target", repos=["acme/private"]), "alice")
        monkeypatch.setattr(
            RunConfig,
            "from_runtime",
            lambda: RunConfig.parse({"thread_id": "thread", "github_login": "alice"}),
        )
        monkeypatch.setattr(
            import_module("openswe.tools.suggest_task"),
            "require_repo_access_for_user",
            AsyncMock(side_effect=HTTPException(403, "no access")),
        )
        result = await tool(summary="Bug", details="Details", repo="acme/private")
        assert result == {"success": False, "error": "no access"}
        async with transaction() as conn:
            assert await conn.scalar(text("SELECT count(*) FROM suggested_task")) == 0

    @pytest.mark.parametrize(
        ("repo", "review_shadow"),
        [("acme/origin", False), ("acme/origin", True), ("acme/other", False)],
    )
    async def test_webhook_sender_without_oauth_can_only_suggest_for_current_repo(
        self, monkeypatch: pytest.MonkeyPatch, repo: str, review_shadow: bool
    ) -> None:
        await WORKSPACES.create(WorkspaceCreate(name="Origin", repos=["acme/origin"]), "alice")
        await index.create("review-thread", runtime="mda")
        monkeypatch.setattr(
            RunConfig,
            "from_runtime",
            lambda: RunConfig.parse(
                {
                    "thread_id": "review-thread",
                    "source": "github",
                    "workspace": "origin",
                    "github_login": "webhook-sender",
                    "repo": {"owner": "acme", "name": "origin"},
                    "pr_number": 7,
                    "review_shadow": review_shadow,
                }
            ),
        )
        module = import_module("openswe.tools.suggest_task")
        monkeypatch.setattr(
            module,
            "require_repo_access_for_user",
            AsyncMock(side_effect=HTTPException(401, "no OAuth")),
        )
        monkeypatch.setattr(module, "require_repo_access_for_workspace", AsyncMock())
        result = await tool(summary="Old bug", details="Fix later", repo=repo)
        async with transaction() as conn:
            count = await conn.scalar(text("SELECT count(*) FROM suggested_task"))
        if repo == "acme/origin":
            assert result["success"] is True
            # A shadow review records nothing anyone on the pull request would see.
            assert count == (0 if review_shadow else 1)
        else:
            assert result == {"success": False, "error": "no OAuth"}
            assert count == 0

    @pytest.mark.parametrize(
        ("summary", "details", "repo", "links"),
        [
            ("   ", "Details", "acme/new-target", []),
            ("x" * 501, "Details", "acme/new-target", []),
            ("Bug", "   ", "acme/new-target", []),
            ("Bug", "x" * 20001, "acme/new-target", []),
            ("Bug", "Details", "not-a-repo", []),
            ("Bug", "Details", "acme/new-target", ["not-a-url"]),
        ],
    )
    async def test_invalid_input_returns_error_without_writing_context_rows(
        self,
        monkeypatch: pytest.MonkeyPatch,
        summary: str,
        details: str,
        repo: str,
        links: list[str],
    ) -> None:
        await WORKSPACES.create(WorkspaceCreate(name="Origin"), "alice")
        await index.create("review-thread", runtime="mda")
        monkeypatch.setattr(
            RunConfig,
            "from_runtime",
            lambda: RunConfig.parse(
                {
                    "thread_id": "review-thread",
                    "source": "github",
                    "workspace": "origin",
                    "github_login": "sender",
                    "pr_url": "https://github.com/acme/origin/pull/7",
                }
            ),
        )
        monkeypatch.setattr(
            import_module("openswe.tools.suggest_task"), "require_repo_access_for_user", AsyncMock()
        )
        result = await tool(summary=summary, details=details, repo=repo, context_links=links)
        assert result["success"] is False
        assert result["error"]
        async with transaction() as conn:
            assert await conn.scalar(text("SELECT count(*) FROM suggested_task")) == 0
            assert await conn.scalar(text("SELECT count(*) FROM repository")) == 0
            assert await conn.scalar(text("SELECT count(*) FROM pull_request")) == 0
