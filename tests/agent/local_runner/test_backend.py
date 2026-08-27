import base64
from typing import Any

import pytest

from agent.local_runner.backend import LocalMachineBackend


class _RecordingBroker:
    """Answers like a connected desktop, remembering what it was asked."""

    def __init__(self, replies: dict[str, dict[str, Any]] | None = None) -> None:
        self.frames: list[dict[str, Any]] = []
        self.replies = replies or {}

    async def call(
        self,
        login: str,
        device_id: str,
        frame: dict[str, Any],
        *,
        timeout: float = 0,
    ) -> dict[str, Any]:
        self.frames.append({**frame, "_login": login, "_device_id": device_id})
        return self.replies.get(frame["type"], {"output": "", "exit_code": 0})


def _backend(broker: _RecordingBroker) -> LocalMachineBackend:
    return LocalMachineBackend(
        login="octocat",
        device_id="abc123",
        thread_id="thread-1",
        project_path="/Users/octocat/dev/app",
        broker=broker,
    )


async def test_every_frame_carries_the_thread_and_project_it_belongs_to() -> None:
    broker = _RecordingBroker({"exec": {"output": "hi", "exit_code": 0}})

    response = await _backend(broker).aexecute("echo hi")

    assert response.output == "hi"
    assert response.exit_code == 0
    frame = broker.frames[0]
    assert frame["command"] == "echo hi"
    assert frame["thread_id"] == "thread-1"
    assert frame["project_path"] == "/Users/octocat/dev/app"
    assert frame["_login"] == "octocat"


async def test_file_operations_are_derived_from_execute() -> None:
    """``BaseSandbox`` builds reads and greps out of shell commands.

    That is the whole reason the relay only has to carry ``execute``, so it is
    worth pinning: a change that stops routing them through the relay would
    silently start reading the *server's* filesystem.
    """
    broker = _RecordingBroker({"exec": {"output": "", "exit_code": 0}})

    await _backend(broker).aread("/Users/octocat/dev/app/README.md")

    assert broker.frames, "read must reach the device"
    assert broker.frames[0]["type"] == "exec"
    encoded = base64.b64encode(b"/Users/octocat/dev/app/README.md").decode("ascii")
    assert encoded in broker.frames[0]["command"]


async def test_uploads_report_per_file_outcomes() -> None:
    broker = _RecordingBroker({"upload": {"results": [{}, {"error": "permission_denied"}]}})

    responses = await _backend(broker).aupload_files(
        [("/app/a.txt", b"one"), ("/app/b.txt", b"two")]
    )

    assert [response.error for response in responses] == [None, "permission_denied"]
    sent = broker.frames[0]["files"]
    assert base64.b64decode(sent[0]["content"]) == b"one"


async def test_downloads_decode_content_and_keep_errors() -> None:
    broker = _RecordingBroker(
        {
            "download": {
                "results": [
                    {"content": base64.b64encode(b"one").decode("ascii")},
                    {"error": "file_not_found"},
                ]
            }
        }
    )

    responses = await _backend(broker).adownload_files(["/app/a.txt", "/app/missing.txt"])

    assert responses[0].content == b"one"
    assert responses[1].content is None
    assert responses[1].error == "file_not_found"


async def test_a_malformed_reply_does_not_pass_for_success() -> None:
    broker = _RecordingBroker({"download": {"results": [{"content": "one"}]}})

    responses = await _backend(broker).adownload_files(["/app/a.txt", "/app/b.txt"])

    assert [response.error for response in responses] == ["file_not_found", "file_not_found"]


def test_the_synchronous_surface_is_refused() -> None:
    with pytest.raises(NotImplementedError, match="async-only"):
        _backend(_RecordingBroker()).execute("ls")
