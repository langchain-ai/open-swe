import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from evals.latency import turn_latency


class TestTurnLatency:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [([20, 80, 160, 250], 120), ([250, 20, 80], 80)],
    )
    def test_median(self, seconds: list[float], expected: float) -> None:
        week = turn_latency.Week.of(seconds)
        assert week is not None
        assert week.p50 == expected

    async def query(self, **query: object) -> SimpleNamespace:
        loop = asyncio.get_running_loop()
        if self.loop is None:
            self.loop = loop
        assert loop is self.loop
        await asyncio.sleep(0)
        self.queries.append(query)
        if query.get("cursor"):
            return SimpleNamespace(items=[SimpleNamespace(latency_seconds=160)], next_cursor=None)
        return SimpleNamespace(items=[SimpleNamespace(latency_seconds=80)], next_cursor="next")

    def test_report_paginates_both_weeks_on_one_loop(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queries: list[dict[str, object]] = []
        client = Mock()
        client.runs.query_v2 = AsyncMock(side_effect=self.query)
        monkeypatch.setattr(turn_latency, "Client", Mock(return_value=client))
        output = tmp_path / "latency.txt"
        monkeypatch.setattr(sys, "argv", ["turn_latency", "--slack", str(output)])

        asyncio.run(turn_latency.main())

        summary = output.read_text()
        assert "This week: median 120s" in summary
        assert "Last week: median 120s" in summary
        assert summary.count("(6 turns)") == 2
        assert len({query["min_start_time"] for query in self.queries}) == 2
        assert sum(query.get("cursor") == "next" for query in self.queries) == 6
