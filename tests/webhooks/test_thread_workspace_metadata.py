from typing import Any

from agent.webhooks import common


class _Client:
    def __init__(self, metadata: dict[str, Any]) -> None:
        self._metadata = metadata

    @property
    def threads(self) -> _Client:
        return self

    async def get(self, thread_id: str) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": self._metadata}


async def test_get_thread_workspace_prefers_workspace_then_environment(monkeypatch) -> None:
    monkeypatch.setattr(
        common, "get_client", lambda url=None: _Client({"workspace": "oss", "environment": "old"})
    )
    assert await common.get_thread_workspace("t1") == "oss"
    monkeypatch.setattr(common, "get_client", lambda url=None: _Client({"environment": "old"}))
    assert await common.get_thread_workspace("t1") == "old"
    monkeypatch.setattr(common, "get_client", lambda url=None: _Client({}))
    assert await common.get_thread_workspace("t1") is None
