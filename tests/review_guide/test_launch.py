from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openswe.review_guide import launch


@pytest.mark.parametrize("is_private", [False, True])
async def test_fork_preserves_visibility_without_copying_source_credentials(
    monkeypatch: pytest.MonkeyPatch, is_private: bool
) -> None:
    threads = SimpleNamespace(
        get=AsyncMock(
            return_value={"metadata": {"visibility": "private", "github_login": "other"}}
        ),
        copy=AsyncMock(return_value={"thread_id": "copy", "metadata": {"github_login": "other"}}),
        update=AsyncMock(),
    )
    client = SimpleNamespace(threads=threads)
    monkeypatch.setattr(launch, "sandbox_host_thread_id", AsyncMock(return_value="source"))

    assert await launch._fork(client, "source", "requester", is_private=is_private) == "copy"

    metadata = threads.update.call_args.kwargs["metadata"]
    assert metadata["visibility"] == ("private" if is_private else "public")
    assert metadata["owner_login"] == "requester"
    assert metadata["github_login"] is None


async def test_requester_who_cannot_read_the_pull_request_gets_no_dm_or_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        launch.User,
        "for_identity",
        AsyncMock(return_value=SimpleNamespace(github_login="alice")),
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
                origin=launch.GuideOrigin(requester_slack_id="U1", is_private=True),
            )
        )

    post_dm.assert_not_awaited()
    create_code_channel.assert_not_awaited()
