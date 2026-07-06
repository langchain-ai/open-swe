from __future__ import annotations


async def test_report_platform_issue_returns_success() -> None:
    from agent.tools.report_platform_issue import report_platform_issue

    assert await report_platform_issue() == "success"


def test_report_platform_issue_exported() -> None:
    from agent.tools import report_platform_issue

    assert callable(report_platform_issue)
