import json
from typing import cast

import httpx2
import pytest

from openswe.github.pull_request_status import PullRequestClient
from openswe.review.vibe_check import _CHUNK_CHARS, VibeCheck
from openswe.utils.jev import SystemOneGateway


class _Pull:
    async def files(self, *, max_pages: int = 1) -> list[dict[str, object]]:
        big_hunk = "@@ -1 +1 @@\n+" + "x" * _CHUNK_CHARS
        return [
            {"filename": "app.py", "patch": f"{big_hunk}\n@@ -9 +9 @@\n+print('here')"},
            {"filename": "tests/test_app.py", "patch": "@@ -1 +1 @@\n+@skip"},
            {"filename": "uv.lock", "patch": "@@ -1 +1 @@\n+print('here')"},
        ]


async def test_vibe_check_keeps_each_files_worst_chunk_and_applies_thresholds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states: list[dict[str, str]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.read())
        state = payload["state"]
        states.append(state)
        debug = 0.9 if "print" in state["diff"] else 0.1
        weakened = 0.6 if "@skip" in state["diff"] else 0.0
        answers = {rule_id: {"type": "noul", "noul": 0.0} for rule_id in payload["questions"]} | {
            "debug-leftovers": {"type": "noul", "noul": debug},
            "weakened-tests": {"type": "noul", "noul": weakened},
        }
        return httpx2.Response(200, json={"model": "jev", "answers": answers})

    client = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2,
        "AsyncClient",
        lambda **kw: client(**{**kw, "transport": httpx2.MockTransport(handle)}),
    )
    check = VibeCheck(cast(PullRequestClient, _Pull()), "abc1234")
    await check.run(SystemOneGateway("key"))

    assert sorted(state["path"] for state in states) == ["app.py", "app.py", "tests/test_app.py"]
    assert [(v.path, v.rule.id, v.level) for v in check.verdicts] == [
        ("app.py", "debug-leftovers", "error"),
        ("tests/test_app.py", "weakened-tests", "warn"),
    ]
    assert check.files_checked == 2
