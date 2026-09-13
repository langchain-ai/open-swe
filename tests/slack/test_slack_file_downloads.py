from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.slack import webhook as slack_webhook


def _file(name: str, url: str, mimetype: str = "application/zip") -> dict[str, Any]:
    return {"name": name, "url_private": url, "mimetype": mimetype}


def test_slack_file_entries_skips_images_and_dedupes_by_url() -> None:
    messages = [
        {
            "files": [
                _file("bundle.zip", "https://files.slack.com/a/bundle.zip"),
                _file("shot.png", "https://files.slack.com/a/shot.png", "image/png"),
                {"name": "no-url.txt"},
            ]
        },
        {"files": [_file("bundle.zip", "https://files.slack.com/a/bundle.zip")]},
    ]

    entries = slack_webhook._slack_file_entries(messages)

    assert [entry["name"] for entry in entries] == ["bundle.zip"]


def test_slack_file_entries_caps_attachment_count() -> None:
    files = [
        _file(f"f{index}.zip", f"https://files.slack.com/a/f{index}.zip") for index in range(25)
    ]

    entries = slack_webhook._slack_file_entries([{"files": files}])

    assert len(entries) == slack_webhook._MAX_SLACK_FILE_ATTACHMENTS


@pytest.mark.parametrize(
    ("name", "url", "expected"),
    [
        ("bundle.zip", "https://files.slack.com/a/x", "bundle.zip"),
        ("../../etc/passwd", "https://files.slack.com/a/fallback.zip", "fallback.zip"),
        ("", "https://files.slack.com/a/from-url.tar.gz", "from-url.tar.gz"),
        ("weird name!.txt", "https://files.slack.com/a/x", "weird_name_.txt"),
    ],
)
def test_sanitize_slack_filename(name: str, url: str, expected: str) -> None:
    assert slack_webhook._sanitize_slack_filename(name, url) == expected


@pytest.mark.asyncio
async def test_download_slack_files_stages_into_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = MagicMock()
    backend.aupload_files = AsyncMock(return_value=[{"error": None}])
    monkeypatch.setattr(
        "agent.sandboxes.lifecycle.ensure_sandbox_for_thread", AsyncMock(return_value=backend)
    )
    monkeypatch.setattr(
        slack_webhook.slack_utils, "download_slack_file", AsyncMock(return_value=(b"zip", None))
    )

    staged = await slack_webhook._download_slack_files_to_sandbox(
        [_file("bundle.zip", "https://files.slack.com/a/bundle.zip")], "thread-1"
    )

    assert staged == [("bundle.zip", f"{slack_webhook._SLACK_FILE_DIR}/bundle.zip")]
    uploaded_path, uploaded_content = backend.aupload_files.await_args.args[0][0]
    assert uploaded_path == f"{slack_webhook._SLACK_FILE_DIR}/bundle.zip"
    assert uploaded_content == b"zip"


@pytest.mark.asyncio
async def test_download_slack_files_skips_failed_download(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = MagicMock()
    backend.aupload_files = AsyncMock(return_value=[{"error": None}])
    monkeypatch.setattr(
        "agent.sandboxes.lifecycle.ensure_sandbox_for_thread", AsyncMock(return_value=backend)
    )
    monkeypatch.setattr(
        slack_webhook.slack_utils,
        "download_slack_file",
        AsyncMock(side_effect=[(None, "file_too_large"), (b"ok", None)]),
    )

    staged = await slack_webhook._download_slack_files_to_sandbox(
        [
            _file("huge.zip", "https://files.slack.com/a/huge.zip"),
            _file("small.zip", "https://files.slack.com/a/small.zip"),
        ],
        "thread-1",
    )

    assert staged == [("small.zip", f"{slack_webhook._SLACK_FILE_DIR}/small.zip")]


@pytest.mark.asyncio
async def test_download_slack_files_tolerates_unreachable_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent.sandboxes.lifecycle.ensure_sandbox_for_thread",
        AsyncMock(side_effect=RuntimeError("sandbox unreachable")),
    )

    staged = await slack_webhook._download_slack_files_to_sandbox(
        [_file("bundle.zip", "https://files.slack.com/a/bundle.zip")], "thread-1"
    )

    assert staged == []


@pytest.mark.asyncio
async def test_download_slack_file_sends_bot_token_only_to_slack_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.slack import client as slack_client

    monkeypatch.setattr(slack_client, "SLACK_BOT_TOKEN", "xoxb-test")
    captured: dict[str, Any] = {}

    async def fake_request(client: Any, method: str, url: str, **kwargs: Any) -> tuple[Any, None]:
        captured["headers_for_url"] = kwargs["headers_for_url"]
        return MagicMock(content=b"payload", raise_for_status=lambda: None), None

    monkeypatch.setattr(slack_client, "request_with_safe_redirects", fake_request)

    content, error = await slack_client.download_slack_file("https://files.slack.com/a/bundle.zip")

    assert (content, error) == (b"payload", None)
    headers_for_url = captured["headers_for_url"]
    slack_url = "https://files.slack.com/a/bundle.zip"
    assert headers_for_url(slack_url, slack_url) == {"Authorization": "Bearer xoxb-test"}
    assert headers_for_url(slack_url, "https://evil.example/steal") is None


@pytest.mark.asyncio
async def test_download_slack_file_rejects_oversize_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.slack import client as slack_client

    monkeypatch.setattr(slack_client, "SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(slack_client, "SLACK_FILE_DOWNLOAD_MAX_BYTES", 3)

    async def fake_request(client: Any, method: str, url: str, **kwargs: Any) -> tuple[Any, None]:
        return MagicMock(content=b"too big", raise_for_status=lambda: None), None

    monkeypatch.setattr(slack_client, "request_with_safe_redirects", fake_request)

    assert await slack_client.download_slack_file("https://files.slack.com/a/x.zip") == (
        None,
        "file_too_large",
    )


@pytest.mark.asyncio
async def test_download_slack_file_requires_a_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.slack import client as slack_client

    monkeypatch.setattr(slack_client, "SLACK_BOT_TOKEN", "")

    assert await slack_client.download_slack_file("https://files.slack.com/a/x.zip") == (
        None,
        "missing_slack_bot_token",
    )
