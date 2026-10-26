import asyncio
import time

import pytest

from openswe.slack import mda_forwarding
from openswe.slack.request import SlackRequest
from openswe.threads import index

pytestmark = pytest.mark.usefixtures("registry_db")

THREAD = "0b9e2d6c-6a3b-5f0e-9a52-2f3d1c4b5a69"


def _request(text: str) -> SlackRequest:
    return SlackRequest(channel_id="C1", thread_ts="1.0", text=text, thread_id=THREAD)


async def test_a_message_remembered_while_another_is_recalled_keeps_both() -> None:
    await index.create(THREAD, runtime="mda", metadata={"title": "kept"})
    await mda_forwarding.remember(_request("A"), None, "1.1")

    recalled, *_ = await asyncio.gather(
        mda_forwarding.recall(THREAD, "1.1"),
        *(mda_forwarding.remember(_request(f"B{n}"), None, f"2.{n}") for n in range(10)),
    )

    assert recalled is not None and recalled[0].text == "A"
    for n in range(10):
        later = await mda_forwarding.recall(THREAD, f"2.{n}")
        assert later is not None and later[0].text == f"B{n}"
    thread = await index.get(THREAD)
    assert thread is not None and thread.metadata["title"] == "kept"


async def test_a_message_is_recalled_once() -> None:
    await index.create(THREAD, runtime="mda", metadata={})
    await mda_forwarding.remember(_request("A"), None, "1.1")

    recalled = await asyncio.gather(*(mda_forwarding.recall(THREAD, "1.1") for _ in range(5)))

    assert [r[0].text for r in recalled if r is not None] == ["A"]


async def test_expired_requests_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    await index.create(THREAD, runtime="mda", metadata={})
    await mda_forwarding.remember(_request("old"), None, "1.1")
    monkeypatch.setattr(time, "time", lambda: 1e12)

    await mda_forwarding.remember(_request("new"), None, "2.1")

    thread = await index.get(THREAD)
    assert thread is not None
    assert list(thread.metadata[mda_forwarding.PENDING_KEY]) == ["2.1"]
    assert await mda_forwarding.recall(THREAD, "1.1") is None
