from __future__ import annotations

import inspect
import uuid
from typing import get_type_hints


async def test_report_platform_issue_returns_uuid7_report_id() -> None:
    from agent.tools.report_platform_issue import report_platform_issue

    result = await report_platform_issue(
        problem_description="The sandbox command timed out",
        keywords=["sandbox", "timeout"],
    )

    assert set(result) == {"report_id"}
    report_id = uuid.UUID(result["report_id"])
    assert report_id.version == 7
    assert report_id.variant == uuid.RFC_4122


def test_report_platform_issue_requires_description_and_keywords() -> None:
    from agent.tools.report_platform_issue import report_platform_issue

    signature = inspect.signature(report_platform_issue)

    assert list(signature.parameters) == ["problem_description", "keywords"]
    assert all(
        parameter.default is inspect.Parameter.empty for parameter in signature.parameters.values()
    )
    type_hints = get_type_hints(report_platform_issue)
    assert type_hints["problem_description"] is str
    assert type_hints["keywords"] == list[str]


def test_report_platform_issue_exported() -> None:
    from agent.tools import report_platform_issue

    assert callable(report_platform_issue)
