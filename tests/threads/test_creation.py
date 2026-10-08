from typing import cast

from langgraph_sdk.client import LangGraphClient

from openswe.threads.creation import TITLE_LOCKED_KEY, ensure_titled_thread


class _Threads:
    def __init__(self, metadata: dict[str, object]) -> None:
        self.metadata = metadata

    async def create(self, **kwargs: object) -> dict[str, object]:
        return {"thread_id": kwargs["thread_id"], "metadata": dict(self.metadata)}

    async def update(self, *, thread_id: str, metadata: dict[str, object]) -> None:
        self.metadata.update(metadata)


class _Client:
    def __init__(self, metadata: dict[str, object]) -> None:
        self.threads = _Threads(metadata)


async def test_system_title_refreshes_until_someone_renames_the_thread() -> None:
    client = _Client({"title": "Review: #7 Old title"})

    await ensure_titled_thread(cast(LangGraphClient, client), "t", title="Review: #7 New title")
    assert client.threads.metadata["title"] == "Review: #7 New title"

    client.threads.metadata.update({"title": "My name for it", TITLE_LOCKED_KEY: True})
    await ensure_titled_thread(cast(LangGraphClient, client), "t", title="Review: #7 Newer title")
    assert client.threads.metadata["title"] == "My name for it"
