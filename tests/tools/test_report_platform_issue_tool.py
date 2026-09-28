import importlib
import logging
import uuid
from typing import Any

import pytest

from agent.tools import report_platform_issue


@pytest.mark.asyncio
async def test_report_platform_issue_survives_undiagnosable_run(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = importlib.import_module("agent.tools.report_platform_issue")

    def no_runtime() -> dict[str, Any]:
        raise RuntimeError("called outside of a runnable context")

    monkeypatch.setattr(module, "get_config", no_runtime)

    with caplog.at_level(logging.WARNING, logger="agent.tools.report_platform_issue"):
        result = await report_platform_issue(problem_description="broken", keywords=["sandbox"])

    assert uuid.UUID(result["report_id"]).version == 7
    record = caplog.records[-1]
    assert record.message == "Platform issue reported"
    assert record.platform_issue_thread_details == {}
