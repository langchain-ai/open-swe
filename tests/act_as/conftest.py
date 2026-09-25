from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.act_as import records
from agent.utils.json_types import JsonObject
from agent.utils.thread_participants import PARTICIPANT_LOGINS_KEY


@pytest.fixture
def thread_metadata(monkeypatch: pytest.MonkeyPatch) -> JsonObject:
    """Metadata of a thread Alice and Bob both posted in; updates write through."""
    stored: JsonObject = {PARTICIPANT_LOGINS_KEY: {"alice": True, "bob": True}}

    async def update(*, thread_id: str, metadata: JsonObject) -> None:
        stored.update(metadata)

    client = SimpleNamespace(
        threads=SimpleNamespace(get=AsyncMock(return_value={"metadata": stored}), update=update)
    )
    monkeypatch.setattr(records, "get_client", lambda: client)
    return stored
