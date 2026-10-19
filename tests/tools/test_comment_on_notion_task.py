import sys

import pytest

import openswe.tools.comment_on_notion_task  # noqa: F401

tool = sys.modules["openswe.tools.comment_on_notion_task"]


@pytest.fixture
def posted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    comments: list[tuple[str, str, str]] = []

    async def post(page_id: str, body: str, *, discussion_id: str = "") -> bool:
        comments.append((page_id, body, discussion_id))
        return True

    monkeypatch.setattr(tool, "post_notion_comment", post)
    return comments


def _config(monkeypatch: pytest.MonkeyPatch, configurable: dict[str, object]) -> None:
    monkeypatch.setattr("openswe.run_config.get_config", lambda: {"configurable": configurable})


async def test_comments_on_the_task_that_started_the_run(
    monkeypatch: pytest.MonkeyPatch, posted: list[tuple[str, str, str]]
) -> None:
    _config(monkeypatch, {"source": "notion", "notion_page": {"id": "page-1"}})

    assert await tool.comment_on_notion_task("  Which footer?  ") == {"success": True}
    assert posted == [("page-1", "Which footer?", "")]


async def test_replies_in_the_discussion_that_started_the_run(
    monkeypatch: pytest.MonkeyPatch, posted: list[tuple[str, str, str]]
) -> None:
    _config(
        monkeypatch,
        {"source": "notion", "notion_page": {"id": "page-1", "discussion_id": "d9"}},
    )

    assert await tool.comment_on_notion_task("Roll back with make rollback") == {"success": True}
    assert posted == [("page-1", "Roll back with make rollback", "d9")]


@pytest.mark.parametrize(
    ("configurable", "text"),
    [
        ({"source": "slack", "notion_page": {"id": "page-1"}}, "hi"),
        ({"source": "notion"}, "hi"),
        ({"source": "notion", "notion_page": {"id": "page-1"}}, "   "),
    ],
)
async def test_refuses_outside_a_notion_run_or_with_empty_text(
    monkeypatch: pytest.MonkeyPatch,
    posted: list[tuple[str, str, str]],
    configurable: dict[str, object],
    text: str,
) -> None:
    _config(monkeypatch, configurable)

    result = await tool.comment_on_notion_task(text)

    assert result["success"] is False
    assert posted == []
