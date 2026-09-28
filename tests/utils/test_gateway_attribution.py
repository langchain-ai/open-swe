from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from agent.run_config import RunConfig
from agent.users.models import User
from agent.utils.gateway_attribution import gateway_metadata_for_run


@pytest.fixture
def users(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    lookups = {
        name: AsyncMock(return_value=None) for name in ("for_identity", "for_login", "for_email")
    }
    for name, lookup in lookups.items():
        monkeypatch.setattr(User, name, lookup)
    return lookups


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["dashboard", "slack", "schedule"])
async def test_current_actor_gets_canonical_id_and_invocation_metadata(
    users: dict[str, AsyncMock], source: str
) -> None:
    person = User(id=UUID("00000000-0000-0000-0000-000000000001"))
    users["for_login"].return_value = person
    metadata = await gateway_metadata_for_run(
        RunConfig(
            github_login="current-sender",
            source=source,
            thread_id="thread",
            invocation_id="invocation",
            slack_thread={"triggering_user_id": "old-slack-sender"},
        )
    )
    assert metadata == {
        "openswe_user_id": str(person.id),
        "thread_id": "thread",
        "invocation_id": "invocation",
    }
    users["for_login"].assert_awaited_once_with("github", "current-sender")


@pytest.mark.asyncio
async def test_numeric_github_webhook_identity_is_preserved(users: dict[str, AsyncMock]) -> None:
    person = User(id=UUID("00000000-0000-0000-0000-000000000001"))
    users["for_identity"].return_value = person
    users["for_login"].return_value = person
    cfg = RunConfig.parse({"github_user_id": 123, "github_login": "sender"})
    assert (await gateway_metadata_for_run(cfg))["openswe_user_id"] == str(person.id)
    users["for_identity"].assert_awaited_once_with("github", "123")
    assert RunConfig.parse({"github_user_id": True}).github_user_id is None


@pytest.mark.asyncio
async def test_conflicting_actor_fields_do_not_charge_either_person(
    users: dict[str, AsyncMock],
) -> None:
    users["for_identity"].return_value = User(id=UUID("00000000-0000-0000-0000-000000000001"))
    users["for_login"].return_value = User(id=UUID("00000000-0000-0000-0000-000000000002"))
    cfg = RunConfig(github_user_id="1", github_login="another-person")
    assert (await gateway_metadata_for_run(cfg))["openswe_user_id"] == "unattributed"


@pytest.mark.asyncio
async def test_slack_email_resolves_to_same_canonical_person(users: dict[str, AsyncMock]) -> None:
    person = User(id=UUID("00000000-0000-0000-0000-000000000001"))
    users["for_email"].return_value = person
    cfg = RunConfig(user_email="sender@example.com")
    assert (await gateway_metadata_for_run(cfg))["openswe_user_id"] == str(person.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cfg",
    [
        RunConfig(),
        RunConfig(source="schedule", slack_thread={"triggering_user_id": "old-sender"}),
        RunConfig(background_task_completion=True, github_login="thread-owner"),
    ],
)
async def test_system_runs_do_not_infer_an_actor_from_thread_provenance(
    users: dict[str, AsyncMock], cfg: RunConfig
) -> None:
    assert (await gateway_metadata_for_run(cfg))["openswe_user_id"] == "unattributed"
    for lookup in users.values():
        lookup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_missing_identity_never_falls_back_to_another_actor(
    users: dict[str, AsyncMock], failure: bool, caplog: pytest.LogCaptureFixture
) -> None:
    if failure:
        users["for_identity"].side_effect = RuntimeError("lookup unavailable")
    cfg = RunConfig(github_user_id="missing", github_login="other", user_email="other@example.com")
    assert (await gateway_metadata_for_run(cfg))["openswe_user_id"] == "unattributed"
    users["for_login"].assert_not_awaited()
    users["for_email"].assert_not_awaited()
    if failure:
        assert "Failed to resolve gateway attribution" in caplog.text
