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
