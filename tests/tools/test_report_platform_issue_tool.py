from __future__ import annotations

import uuid


async def test_report_platform_issue_returns_uuid7_report_id() -> None:
    from agent.tools.report_platform_issue import report_platform_issue

    result = await report_platform_issue()

    assert set(result) == {"report_id"}
    report_id = uuid.UUID(result["report_id"])
    assert report_id.version == 7
    assert report_id.variant == uuid.RFC_4122


def test_report_platform_issue_exported() -> None:
    from agent.tools import report_platform_issue

    assert callable(report_platform_issue)
