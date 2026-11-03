from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from openswe.review_guide import launch


@pytest.mark.parametrize("is_private", [False, True])
async def test_fork_preserves_visibility_without_copying_source_credentials(
    monkeypatch: pytest.MonkeyPatch, is_private: bool
) -> None:
    source = SimpleNamespace(metadata={"visibility": "private", "github_login": "other"})
    copied = SimpleNamespace(thread_id="copy", metadata={"github_login": "other"})
    monkeypatch.setattr(launch.THREADS, "get", AsyncMock(return_value=source))
    monkeypatch.setattr(launch.THREADS, "copy", AsyncMock(return_value=copied))
    update = AsyncMock()
    monkeypatch.setattr(launch.THREADS, "update", update)
    monkeypatch.setattr(launch, "sandbox_host_thread_id", AsyncMock(return_value="source"))

    assert await launch._fork("source", "requester", is_private=is_private) == "copy"

    metadata = update.call_args.args[1]
    assert metadata["visibility"] == ("private" if is_private else "public")
    assert metadata["owner_login"] == "requester"
    assert metadata["github_login"] is None


async def test_requester_who_cannot_read_the_pull_request_gets_no_dm_or_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        launch.User,
        "get",
        AsyncMock(return_value=SimpleNamespace(id=uuid4(), github_login="alice")),
    )
    monkeypatch.setattr(launch, "get_valid_access_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(launch, "fetch_head", AsyncMock(return_value=None))
    post_dm = AsyncMock()
    create_code_channel = AsyncMock()
    monkeypatch.setattr(launch, "post_dm", post_dm)
    monkeypatch.setattr(launch, "create_code_channel", create_code_channel)

    with pytest.raises(launch.GuideStartError, match="cannot read that pull request"):
        await launch.start_review_guide(
            launch.GuideStart(
                owner="acme",
                repo="private",
                number=1,
                origin=launch.GuideOrigin(requester_id=uuid4(), is_private=True),
                code_channel=True,
            )
        )

    post_dm.assert_not_awaited()
    create_code_channel.assert_not_awaited()
