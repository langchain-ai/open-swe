"""Provider behavior against the Mainbrella HTTP contract."""

import asyncio
import json
from collections.abc import Callable

import httpx2
import pytest

from openswe.sandboxes.providers import mainbrella
from openswe.sandboxes.providers.registry import create_sandbox

GENERATION = "2026-10-08T12:00:00.000Z"
SANDBOX_ID = f"mainbrella:c1@{GENERATION}"
EXECUTION_ID = "b4588e10-680f-44ab-a64a-eb4a7e861221"


def use_api(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> None:
    original_client = httpx2.AsyncClient

    def client(
        *, base_url: str, headers: dict[str, str], timeout: float, follow_redirects: bool
    ) -> httpx2.AsyncClient:
        return original_client(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
            follow_redirects=follow_redirects,
            transport=httpx2.MockTransport(handler),
        )

    monkeypatch.setenv("SANDBOX_TYPE", "mainbrella")
    monkeypatch.setenv("MAINBRELLA_API_KEY", "test-api-key")
    monkeypatch.setattr(httpx2, "AsyncClient", client)
    monkeypatch.setattr(mainbrella, "_POLL_INTERVAL_SECONDS", 0)


def command_result(**changes: object) -> dict[str, object]:
    return {
        "stdout": "",
        "stderr": "",
        "exitCode": 0,
        "timedOut": False,
        "outputTruncated": False,
        **changes,
    }


async def test_creation_retries_same_reservation_and_binds_returned_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keys: set[str] = set()
    attempts = 0

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        assert request.method == "POST" and request.url.path == "/containers"
        assert request.headers["Authorization"] == "Bearer test-api-key"
        assert json.loads(request.content) == {"imageId": "custom-image", "size": "medium"}
        keys.add(request.headers["Idempotency-Key"])
        attempts += 1
        if attempts == 1:
            raise httpx2.ReadTimeout("lost response", request=request)
        status = "starting" if attempts == 2 else "running"
        return httpx2.Response(
            200,
            json={
                "containers": [
                    {"id": "small", "createdAt": "2026-10-08T10:00:00.000Z", "status": "running"},
                    {"id": "c1", "createdAt": GENERATION, "status": status},
                ],
                "creation": {
                    "id": "creation-1",
                    "containerId": "c1",
                    "createdAt": GENERATION,
                    "status": status,
                },
            },
        )

    use_api(monkeypatch, handle)
    monkeypatch.setenv("MAINBRELLA_IMAGE_ID", "custom-image")
    monkeypatch.setenv("MAINBRELLA_SANDBOX_SIZE", "medium")
    backend = await create_sandbox()

    assert backend.id == SANDBOX_ID
    assert len(keys) == 1


@pytest.mark.parametrize("state", ["running", "replaced", "stopped", "unreachable"])
async def test_reconnect_preserves_exact_generation_without_provisioning(
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "GET" and request.url.path == "/containers"
        if state == "unreachable":
            return httpx2.Response(503, json={"error": "containers_unavailable"})
        return httpx2.Response(
            200,
            json={
                "containers": [
                    {
                        "id": "c1",
                        "createdAt": "2026-10-08T13:00:00.000Z"
                        if state == "replaced"
                        else GENERATION,
                        "status": "stopped" if state == "stopped" else "running",
                    }
                ]
            },
        )

    use_api(monkeypatch, handle)
    if state == "running":
        assert (await create_sandbox(SANDBOX_ID)).id == SANDBOX_ID
    else:
        with pytest.raises((RuntimeError, httpx2.HTTPStatusError)):
            await create_sandbox(SANDBOX_ID)


async def test_foreground_failure_keeps_exit_status_and_never_replays_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = 0

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        assert request.url.path == "/containers/exec"
        assert dict(request.url.params) == {"id": "c1", "createdAt": GENERATION}
        assert json.loads(request.content)["timeoutMs"] == 60000
        if requests == 1:
            return httpx2.Response(
                200,
                json=command_result(
                    stdout="partial", stderr="failure", exitCode=2, outputTruncated=True
                ),
            )
        raise httpx2.ReadTimeout("ambiguous execution", request=request)

    use_api(monkeypatch, handle)
    backend = mainbrella.MainbrellaSandbox(mainbrella.MainbrellaProvider(), "c1", GENERATION)
    result = await backend.aexecute("false")
    assert (result.output, result.exit_code, result.truncated) == ("partial\nfailure", 2, True)
    with pytest.raises(httpx2.ReadTimeout):
        await backend.aexecute("touch /workspace/once")
    assert requests == 2


async def test_long_command_polls_managed_job_and_reports_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    polls = 0

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal polls
        assert dict(request.url.params) == {"id": "c1", "createdAt": GENERATION}
        if request.method == "POST":
            assert request.url.path == "/containers/executions"
            assert request.headers["Idempotency-Key"]
            assert json.loads(request.content)["timeoutMs"] == 300000
            return httpx2.Response(
                202,
                json={
                    "id": EXECUTION_ID,
                    "status": "starting",
                    "exitCode": None,
                    "timedOut": False,
                    "outputTruncated": False,
                },
            )
        assert (
            request.method == "GET" and request.url.path == f"/containers/executions/{EXECUTION_ID}"
        )
        polls += 1
        return httpx2.Response(
            200,
            json={
                "id": EXECUTION_ID,
                "status": "running" if polls == 1 else "timed_out",
                **command_result(stdout="progress", exitCode=None, timedOut=polls > 1),
            },
        )

    use_api(monkeypatch, handle)
    backend = mainbrella.MainbrellaSandbox(mainbrella.MainbrellaProvider(), "c1", GENERATION)
    result = await backend.aexecute("sleep 300", timeout=300)
    assert result.exit_code == 124
    assert "progress" in result.output and "timed out" in result.output


async def test_canceling_wait_revokes_managed_job_and_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canceled: set[str] = set()

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert dict(request.url.params) == {"id": "c1", "createdAt": GENERATION}
        if request.method == "POST":
            return httpx2.Response(
                202, json={"id": EXECUTION_ID, "status": "running", **command_result()}
            )
        if request.method == "DELETE":
            canceled.add(request.url.path)
            return httpx2.Response(202, json={})
        raise asyncio.CancelledError

    use_api(monkeypatch, handle)
    backend = mainbrella.MainbrellaSandbox(mainbrella.MainbrellaProvider(), "c1", GENERATION)
    with pytest.raises(asyncio.CancelledError):
        await backend.aexecute("sleep 300", timeout=300)
    assert canceled == {f"/containers/executions/{EXECUTION_ID}"}


async def test_binary_transfers_create_parents_and_preserve_batch_partial_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contents: dict[str, bytes] = {}
    parents: set[str] = set()

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.url.params["id"] == "c1" and request.url.params["createdAt"] == GENERATION
        if request.url.path == "/containers/exec":
            command = json.loads(request.content)["command"]
            if "mkdir -p -- /workspace/nested" in command:
                parents.add("/workspace/nested")
            return httpx2.Response(200, json=command_result())
        assert request.url.path == "/containers/files"
        path = request.url.params["path"]
        if request.method == "PUT":
            assert "/workspace/nested" in parents
            assert request.headers["Content-Type"] == "application/octet-stream"
            contents[path] = request.content
            return httpx2.Response(200, json={"path": path, "size": len(request.content)})
        if path not in contents:
            return httpx2.Response(404, json={"error": "file_not_found"})
        return httpx2.Response(200, content=contents[path])

    use_api(monkeypatch, handle)
    backend = mainbrella.MainbrellaSandbox(mainbrella.MainbrellaProvider(), "c1", GENERATION)
    path = "/workspace/nested/binary.dat"
    binary = bytes([0, 128, 255])
    uploaded = await backend.aupload_files(
        [
            ("/workspace/../invalid", b"no"),
            ("/workspace/large", b"x" * (1024 * 1024 + 1)),
            (path, binary),
        ]
    )
    assert [result.error for result in uploaded] == ["invalid_path", "file_too_large", None]
    downloaded = await backend.adownload_files(["/workspace/missing", path])
    assert downloaded[0].error == "file_not_found"
    assert downloaded[1].content == binary and downloaded[1].error is None
