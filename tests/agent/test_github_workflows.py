from collections.abc import Callable
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from openswe.tools.github_workflows import manage_workflow_allowlist, trigger_github_workflow
from tests.conftest import FakeStore


@pytest.fixture(autouse=True)
def requester(grant_tool_access: Callable[..., None]):
    grant_tool_access(admin=True, admin_surface=True)
    with (
        patch(
            "openswe.run_config.get_config",
            return_value={"configurable": {"github_login": "admin"}},
        ),
        patch("openswe.tools.github_workflows.require_repo_access_for_user", AsyncMock()),
        patch("openswe.github.workflows.WorkflowPolicies.require_repository", AsyncMock()),
    ):
        yield


async def test_admin_gate_and_revocation(
    fake_store: FakeStore, grant_tool_access: Callable[..., None]
):
    grant_tool_access(admin=False, admin_surface=True)
    assert "error" in await manage_workflow_allowlist("set", "o/r", "deploy.yml", "main")
    assert not fake_store.items
    grant_tool_access(admin=True, admin_surface=True)
    await manage_workflow_allowlist("set", "o/r", "deploy.yml", "main")
    await manage_workflow_allowlist("remove", "o/r", "deploy.yml")
    with pytest.raises(ValueError, match="not allowlisted"):
        await trigger_github_workflow("o/r", "deploy.yml")


async def test_dispatch_respects_fixed_ref_inputs_and_repo_access(fake_store: FakeStore):
    repo = AsyncMock()
    repo.can_write.return_value = True
    scopes: list[dict[str, str]] = []

    @asynccontextmanager
    async def as_app(owner, name, *, permissions):
        from unittest.mock import Mock

        scopes.append(permissions)
        yield Mock(repo=lambda owner, name: repo)

    with patch("openswe.tools.github_workflows.GitHubClient.as_app", as_app):
        await manage_workflow_allowlist("set", "o/r", "deploy.yml", "main", {"env": "staging"})
        with pytest.raises(ValueError, match="admin-fixed"):
            await trigger_github_workflow("o/r", "deploy.yml", {"env": "prod"})
        await manage_workflow_allowlist(
            "set", "o/r", "deploy.yml", "main", {"env": "staging"}, True
        )
        with pytest.raises(ValueError, match="override"):
            await trigger_github_workflow("o/r", "deploy.yml", {"env": "prod"})
        await trigger_github_workflow("o/r", "deploy.yml", {"version": "v1"})
        repo.dispatch_workflow.assert_awaited_once_with(
            "deploy.yml", "main", {"env": "staging", "version": "v1"}
        )
        assert scopes == [{"metadata": "read"}, {"actions": "write"}]
        repo.dispatch_workflow.reset_mock()
        repo.can_write.return_value = False
        with pytest.raises(ValueError, match="write access"):
            await trigger_github_workflow("o/r", "deploy.yml")
        repo.dispatch_workflow.assert_not_awaited()


async def test_policies_do_not_cross_workspaces(fake_store: FakeStore):
    from openswe.github.workflows import WorkflowPolicies, WorkflowRule

    await WorkflowPolicies("one").set("o/r", "deploy.yml", WorkflowRule(ref="main"))
    assert await WorkflowPolicies("two").read("o/r", "deploy.yml") is None
    assert await WorkflowPolicies("one").read("o/r", "deploy.yml") is not None
