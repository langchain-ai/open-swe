import pytest

from agent.config import ENV
from agent.dashboard.user_preferences import (
    UserPreferencesUpdate,
    get_user_preferences,
    set_user_preferences,
)
from agent.workspaces import routing
from agent.workspaces.store import WORKSPACES, WorkspaceCreate, WorkspaceUpdate
from tests.conftest import FakeStore

# Workspaces are rows; user preferences are still Store records, so the tests
# that read a preference keep the store double as well.
pytestmark = pytest.mark.usefixtures("registry_db")


async def test_default_workspace_preference_round_trips(fake_store: FakeStore) -> None:
    assert (await get_user_preferences("alice"))["default_workspace"] is None
    await set_user_preferences(
        "alice", UserPreferencesUpdate(default_visibility="public", default_workspace=" OSS ")
    )
    assert (await get_user_preferences("alice"))["default_workspace"] == "oss"


@pytest.fixture(autouse=True)
def _clear_routing_cache() -> None:
    routing.invalidate_routing_cache()


async def _seed() -> None:
    await WORKSPACES.create(WorkspaceCreate(name="Default"), "alice")
    await WORKSPACES.create(
        WorkspaceCreate(name="OSS", repos=["acme/oss"], slack_channel_ids=["C0SS"]), "alice"
    )
    routing.invalidate_routing_cache()


async def test_thread_wins_over_everything() -> None:
    await _seed()
    result = await routing.resolve_workspace(
        thread_workspace="default", tag="oss", repo=("acme", "oss"), slack_channel_id="C0SS"
    )
    assert result == routing.WorkspaceResolution("default", "thread")


async def test_tag_then_repo_then_channel_then_user_default(fake_store: FakeStore) -> None:
    await _seed()
    await set_user_preferences(
        "alice", UserPreferencesUpdate(default_visibility="public", default_workspace="oss")
    )
    assert (await routing.resolve_workspace(tag="oss")).resolved_by == "tag"
    assert (
        await routing.resolve_workspace(tag="missing", repo=("acme", "oss"))
    ).resolved_by == "repo"
    assert (
        await routing.resolve_workspace(repo=("acme", "unowned"), slack_channel_id="C0SS")
    ).slug == "oss"
    assert (await routing.resolve_workspace(slack_channel_id="C0SS")).resolved_by == "channel"
    assert (await routing.resolve_workspace(login="alice")).resolved_by == "user_default"
    assert (await routing.resolve_workspace(login="bob")) == routing.WorkspaceResolution(
        "default", "instance_default"
    )


async def test_unknown_user_default_is_ignored(fake_store: FakeStore) -> None:
    await _seed()
    await set_user_preferences(
        "alice", UserPreferencesUpdate(default_visibility="public", default_workspace="gone")
    )
    assert (await routing.resolve_workspace(login="alice")).resolved_by == "instance_default"


async def test_unassigned_repo_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    await _seed()
    assert await routing.repo_is_routable("acme", "unowned") is True
    monkeypatch.setenv(ENV.OPEN_SWE_UNASSIGNED_REPO_WORKSPACE.name, "ignore")
    assert await routing.repo_is_routable("acme", "unowned") is False
    assert await routing.repo_is_routable("acme", "oss") is True


async def test_writes_are_visible_to_routing_immediately() -> None:
    assert await routing.workspace_for_repo("acme", "oss") is None
    await WORKSPACES.create(WorkspaceCreate(name="OSS", repos=["acme/oss"]), "alice")
    assert await routing.workspace_for_repo("acme", "oss") == "oss"
    await WORKSPACES.apply_update("oss", WorkspaceUpdate(repos=["acme/other"]))
    assert await routing.workspace_for_repo("acme", "oss") is None
    assert await routing.workspace_for_repo("acme", "other") == "oss"


async def test_an_unreadable_database_is_not_an_unowned_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the GitHub webhook route fails closed; everything else picks ``default``."""
    monkeypatch.delenv("POSTGRES_URI", raising=False)

    with pytest.raises(routing.WorkspaceLookupError):
        await routing.repo_is_routable("acme", "oss")

    assert await routing.workspace_for_repo("acme", "oss") is None
    assert await routing.workspace_for_slack_channel("C0SS") is None
    assert (await routing.resolve_workspace(repo=("acme", "oss"))).resolved_by == "instance_default"
