from agent.utils.comments import get_recent_comments


def test_get_recent_comments_returns_none_for_empty() -> None:
    assert get_recent_comments([], ("🤖 **Agent Response**",)) is None


def test_get_recent_comments_returns_none_when_newest_is_bot_message() -> None:
    comments = [
        {"body": "🤖 **Agent Response** latest", "createdAt": "2024-01-03T00:00:00Z"},
        {"body": "user comment", "createdAt": "2024-01-02T00:00:00Z"},
    ]

    assert get_recent_comments(comments, ("🤖 **Agent Response**",)) is None


def test_get_recent_comments_collects_since_last_bot_message() -> None:
    comments = [
        {"body": "first user", "createdAt": "2024-01-01T00:00:00Z"},
        {"body": "🤖 **Agent Response** done", "createdAt": "2024-01-02T00:00:00Z"},
        {"body": "follow up 1", "createdAt": "2024-01-03T00:00:00Z"},
        {"body": "follow up 2", "createdAt": "2024-01-04T00:00:00Z"},
    ]

    result = get_recent_comments(comments, ("🤖 **Agent Response**",))
    assert result is not None
    assert [comment["body"] for comment in result] == ["follow up 1", "follow up 2"]
