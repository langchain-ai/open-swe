from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.utils import slack as slack_utils


def test_is_text_like_slack_file_image_excluded() -> None:
    assert (
        slack_utils.is_text_like_slack_file(
            {"mimetype": "image/png", "url_private": "https://files.slack.com/x.png"}
        )
        is False
    )


def test_is_text_like_slack_file_text_mimetype() -> None:
    assert (
        slack_utils.is_text_like_slack_file(
            {"mimetype": "text/plain", "url_private": "https://files.slack.com/x.txt"}
        )
        is True
    )


def test_is_text_like_slack_file_python_filetype() -> None:
    assert (
        slack_utils.is_text_like_slack_file(
            {
                "mimetype": "application/octet-stream",
                "filetype": "python",
                "url_private": "https://files.slack.com/x.py",
            }
        )
        is True
    )


def test_is_text_like_slack_file_json_mimetype() -> None:
    assert (
        slack_utils.is_text_like_slack_file(
            {
                "mimetype": "application/json",
                "url_private": "https://files.slack.com/x.json",
            }
        )
        is True
    )


def test_is_text_like_slack_file_snippet_mode() -> None:
    assert (
        slack_utils.is_text_like_slack_file(
            {
                "mimetype": "",
                "mode": "snippet",
                "url_private": "https://files.slack.com/x",
            }
        )
        is True
    )


def test_is_text_like_slack_file_no_url() -> None:
    assert (
        slack_utils.is_text_like_slack_file({"mimetype": "text/plain", "url_private": ""}) is False
    )


def test_is_text_like_slack_file_non_dict() -> None:
    assert slack_utils.is_text_like_slack_file("not a dict") is False  # type: ignore[arg-type]


def test_format_slack_file_snippet_with_language() -> None:
    file_info = {
        "name": "config.json",
        "filetype": "json",
        "pretty_type": "JSON",
    }
    out = slack_utils.format_slack_file_snippet(file_info, '{"a": 1}')
    assert "`config.json`" in out
    assert "(JSON)" in out
    assert "```json" in out
    assert '{"a": 1}' in out
    assert out.rstrip().endswith("```")


def test_format_slack_file_snippet_unknown_language() -> None:
    out = slack_utils.format_slack_file_snippet({"name": "thing.bin"}, "raw text")
    assert "`thing.bin`" in out
    assert "```\n" in out


def _content_response(content: bytes) -> MagicMock:
    response = MagicMock()
    response.content = content
    response.raise_for_status.return_value = None
    return response


@pytest.mark.asyncio
async def test_fetch_slack_file_text_returns_decoded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")
    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=_content_response(b"hello world"))

    result = await slack_utils.fetch_slack_file_text(
        {"url_private": "https://files.slack.com/x.txt"}, http_client
    )

    assert result == "hello world"
    http_client.get.assert_awaited_once()
    _, kwargs = http_client.get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer xoxb-test"
    assert kwargs["follow_redirects"] is True


@pytest.mark.asyncio
async def test_fetch_slack_file_text_truncates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")
    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=_content_response(b"abcdefghij"))

    result = await slack_utils.fetch_slack_file_text(
        {"url_private": "https://files.slack.com/x.txt"},
        http_client,
        max_bytes=4,
    )

    assert result is not None
    assert result.startswith("abcd")
    assert "[truncated]" in result


@pytest.mark.asyncio
async def test_fetch_slack_file_text_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "")
    http_client = MagicMock()
    http_client.get = AsyncMock()

    result = await slack_utils.fetch_slack_file_text(
        {"url_private": "https://files.slack.com/x.txt"}, http_client
    )

    assert result is None
    http_client.get.assert_not_called()


@pytest.mark.asyncio
async def test_extract_slack_text_files_from_messages_inlines_snippets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    messages = [
        {
            "text": "here is some code",
            "files": [
                {
                    "id": "F1",
                    "name": "snippet.py",
                    "filetype": "python",
                    "pretty_type": "Python",
                    "mimetype": "text/x-python",
                    "url_private": "https://files.slack.com/F1",
                },
                {
                    "id": "IMG1",
                    "mimetype": "image/png",
                    "url_private": "https://files.slack.com/img.png",
                },
            ],
        },
        {
            "text": "and the same file again",
            "files": [
                {
                    "id": "F1",
                    "name": "snippet.py",
                    "filetype": "python",
                    "mimetype": "text/x-python",
                    "url_private": "https://files.slack.com/F1",
                }
            ],
        },
    ]

    http_client = MagicMock()
    http_client.get = AsyncMock(return_value=_content_response(b"print('hello')\n"))

    section = await slack_utils.extract_slack_text_files_from_messages(messages, http_client)

    assert "## Attached Snippets" in section
    assert "`snippet.py`" in section
    assert "```python" in section
    assert "print('hello')" in section
    assert http_client.get.await_count == 1


@pytest.mark.asyncio
async def test_extract_slack_text_files_from_messages_no_files() -> None:
    section = await slack_utils.extract_slack_text_files_from_messages(
        [{"text": "hi", "files": []}], MagicMock()
    )
    assert section == ""


@pytest.mark.asyncio
async def test_extract_slack_text_files_from_messages_only_images() -> None:
    http_client = MagicMock()
    http_client.get = AsyncMock()

    section = await slack_utils.extract_slack_text_files_from_messages(
        [
            {
                "files": [
                    {
                        "mimetype": "image/png",
                        "url_private": "https://files.slack.com/img.png",
                    }
                ]
            }
        ],
        http_client,
    )

    assert section == ""
    http_client.get.assert_not_called()
