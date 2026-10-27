import asyncio
import base64
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from io import BytesIO
from unittest.mock import AsyncMock
from uuid import uuid7

import pytest
from PIL import Image
from pydantic import JsonValue, TypeAdapter
from runtime_api.frontend_review import (
    CaptureFrontendReview,
    FrontendReviewArguments,
    ReadFrontendReview,
    ReadFrontendReviewArguments,
)
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve

from openswe.agent_api import server
from openswe.agent_api.server import RefusedCallError
from openswe.agent_api.tokens import RemoteRun
from openswe.frontend_review import agent_api, capture
from openswe.frontend_review.models import FrontendReviewRun
from openswe.frontend_review.pixels import compare
from openswe.github.pull_requests import PullRequest


def _png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_exact_mask_uses_threshold_and_preserves_unchanged_pixels() -> None:
    before = Image.new("RGB", (64, 32), (20, 20, 20))
    after = before.copy()
    after.putpixel((1, 1), (38, 20, 20))
    for x in range(4):
        after.putpixel((x, 2), (39, 20, 20))
    comparison = compare(_png(before), _png(after), threshold=18)
    assert comparison.metrics.changed_pixels == 4
    assert comparison.metrics.changed_ratio == 4 / (64 * 32)
    with Image.open(BytesIO(comparison.mask)) as mask:
        assert mask.getpixel((1, 1)) == (226, 61, 117, 0)
        assert mask.getpixel((1, 2)) == (226, 61, 117, 255)
    assert len(comparison.regions) == 1
    assert comparison.regions[0].pixels == 4
    with pytest.raises(ValueError, match="same dimensions"):
        compare(_png(before), _png(Image.new("RGB", (32, 32))))

    diagonal_before = Image.new("RGB", (64, 64))
    diagonal_after = diagonal_before.copy()
    for pixel in range(30, 34):
        diagonal_after.putpixel((pixel, pixel), (255, 255, 255))
    [region] = compare(_png(diagonal_before), _png(diagonal_after)).regions
    assert region.model_dump() == {"x": 30, "y": 30, "width": 4, "height": 4, "pixels": 4}


async def test_dummy_capture_finishes_blocked_without_browser_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = FrontendReviewRun(
        id=uuid7(),
        pull_request_id=uuid7(),
        base_sha="a" * 40,
        head_sha="b" * 40,
        base_url="https://base.vercel.app",
        head_url="https://head.vercel.app",
        paths=["/"],
        auth_mode="dummy",
    )
    run.pull_request = PullRequest(owner="acme", repo="widgets", number=7)
    finish = AsyncMock()
    monkeypatch.setattr(capture, "finish_review", finish)
    browser = AsyncMock(side_effect=AssertionError("must not open a browser"))
    monkeypatch.setattr(capture, "_browser", browser)

    await capture.capture_run(run)

    assert not browser.called
    assert finish.call_args.kwargs["status"] == "blocked"
    assert finish.call_args.kwargs["assets"] == []


@pytest.mark.parametrize(
    "http_status,late_status,expected_status",
    [
        (200, None, "complete"),
        (401, None, "blocked"),
        (403, None, "blocked"),
        (503, None, "failed"),
        (None, None, "failed"),
        (200, 503, "failed"),
    ],
)
async def test_capture_uses_main_document_http_status_instead_of_rendered_error_pages(
    monkeypatch: pytest.MonkeyPatch,
    http_status: int | None,
    late_status: int | None,
    expected_status: str,
) -> None:
    if http_status is None:
        timeout = asyncio.timeout
        monkeypatch.setattr(
            asyncio, "timeout", lambda delay: timeout(0.1 if delay == 45 else delay)
        )
    screenshot = _png(Image.new("RGB", (2, 1)))
    screenshots: list[str] = []
    objects = TypeAdapter(dict[str, JsonValue])

    async def browser(connection: ServerConnection) -> None:
        location = ""
        async for raw in connection:
            command = objects.validate_json(raw)
            method = command.get("method")
            params = objects.validate_python(command.get("params", {}))
            result: dict[str, JsonValue] = {}
            if method == "Target.createBrowserContext":
                result = {"browserContextId": "isolated-context"}
            elif method == "Target.createTarget":
                result = {"targetId": "target"}
            elif method == "Target.attachToTarget":
                result = {"sessionId": "page-session"}
            elif method == "Page.navigate":
                location = str(params["url"])
                result = {"frameId": "main-frame"}
                for session, frame, kind, status in (
                    ("page-session", "main-frame", "Document", http_status),
                    ("page-session", "child-frame", "Document", 503),
                    ("other-session", "main-frame", "Document", 503),
                    ("page-session", "main-frame", "XHR", 503),
                ):
                    await connection.send(
                        json.dumps(
                            {
                                "method": "Network.responseReceived",
                                "sessionId": session,
                                "params": {
                                    "frameId": frame,
                                    "type": kind,
                                    "response": {"status": status},
                                },
                            }
                        )
                    )
            elif method == "Runtime.evaluate":
                expression = params.get("expression")
                if (
                    late_status is not None
                    and isinstance(expression, str)
                    and expression.startswith("(async()=>")
                ):
                    await connection.send(
                        json.dumps(
                            {
                                "method": "Network.responseReceived",
                                "sessionId": "page-session",
                                "params": {
                                    "frameId": "main-frame",
                                    "type": "Document",
                                    "response": {"status": late_status},
                                },
                            }
                        )
                    )
                value: JsonValue = (
                    "complete"
                    if expression == "document.readyState"
                    else location
                    if expression == "location.href"
                    else isinstance(expression, str) and expression.startswith("(async()=>")
                )
                result = {"result": {"value": value}}
            elif method == "Page.captureScreenshot":
                screenshots.append(location)
                result = {"data": base64.b64encode(screenshot).decode("ascii")}
            await connection.send(json.dumps({"id": command["id"], "result": result}))

    run = FrontendReviewRun(
        id=uuid7(),
        pull_request_id=uuid7(),
        base_sha="a" * 40,
        head_sha="b" * 40,
        base_url="https://base.vercel.app",
        head_url="https://head.vercel.app",
        paths=["/"],
        auth_mode="none",
    )
    run.pull_request = PullRequest(owner="acme", repo="widgets", number=7)
    finish = AsyncMock()
    monkeypatch.setattr(capture, "finish_review", finish)
    async with serve(browser, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        @asynccontextmanager
        async def disposable_browser(_name: str) -> AsyncIterator[capture._CDP]:
            async with connect(f"ws://127.0.0.1:{port}") as connection:
                yield capture._CDP(connection)

        monkeypatch.setattr(capture, "_browser", disposable_browser)
        await asyncio.wait_for(capture.capture_run(run), timeout=5)

    assert finish.call_args.kwargs["status"] == expected_status
    if expected_status == "complete":
        assert screenshots == ["https://base.vercel.app/", "https://head.vercel.app/"]
        assert len(finish.call_args.kwargs["report"].scenarios) == 1
    else:
        assert screenshots == []
        assert finish.call_args.kwargs["report"] is None
        assert finish.call_args.kwargs["assets"] == []


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/",
        "https://metadata.google.internal/",
        "https://smith.langchain.com.evil.test/",
        "https://preview.vercel.app/?token=secret",
        "https://user:password@preview.vercel.app/",
    ],
)
def test_capture_refuses_unapproved_or_credential_bearing_urls(url: str) -> None:
    with pytest.raises(capture.CaptureBlockedError):
        capture.validate_preview_url(url)


async def test_agent_capture_requires_token_bound_pr_before_creating_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create = AsyncMock(side_effect=AssertionError("must not create an unbound review"))
    monkeypatch.setattr(agent_api, "create_review", create)
    run = RemoteRun(thread_id="thread-1", assistant_id="reviewer", configurable={})

    with pytest.raises(RefusedCallError):
        await agent_api.capture(
            run,
            CaptureFrontendReview(
                request=FrontendReviewArguments(
                    base_url="https://base.vercel.app",
                    head_url="https://head.vercel.app",
                )
            ),
        )
    assert not create.called


async def test_agent_cannot_inspect_evidence_from_another_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid7()
    evidence = FrontendReviewRun(
        id=run_id,
        pull_request_id=uuid7(),
        base_sha="a" * 40,
        head_sha="c" * 40,
        base_url="https://base.vercel.app",
        head_url="https://head.vercel.app",
        paths=["/"],
        auth_mode="none",
    )
    monkeypatch.setattr(agent_api, "get_review", AsyncMock(return_value=evidence))
    token = RemoteRun(
        thread_id="thread-1",
        assistant_id="reviewer",
        configurable={
            "repo": {"owner": "acme", "name": "widgets"},
            "pr_number": 7,
            "head_sha": "b" * 40,
        },
    )
    with pytest.raises(RefusedCallError, match="does not belong"):
        await agent_api.inspect(
            token,
            ReadFrontendReview(
                request=ReadFrontendReviewArguments(
                    review_id=run_id,
                )
            ),
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        "files.write",
        "operation",
        "settle_review_check",
        "launch_worker",
        "capture_frontend_review",
    ],
)
async def test_dedicated_assessment_refuses_coding_and_publication_apis(endpoint: str) -> None:
    run = RemoteRun(
        thread_id="visual-thread",
        assistant_id="reviewer",
        configurable={
            "frontend_review_id": str(uuid7()),
        },
    )
    with pytest.raises(RefusedCallError, match="cannot call"):
        await server._answer(run, endpoint, {})  # noqa: SLF001


async def test_dedicated_assessment_never_prepares_the_regular_code_reviewer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain_core.runnables import RunnableConfig
    from runtime_api.loop import PREPARE, Prepare

    from openswe.agent_api.graph import RemoteGraph
    from openswe.frontend_review import runtime
    from openswe.reviewer import ReviewerModelChoice, ReviewerModels
    from openswe.sandboxes.tool_runtime import ToolSurface

    async def forbidden_plan(_: RunnableConfig, _surface: ToolSurface) -> None:
        raise AssertionError("Must not prepare a code review, checkout, or GitHub check")

    choice = ReviewerModelChoice("openai:gpt-6.1-sol", {})
    monkeypatch.setattr(
        runtime,
        "resolve_reviewer_models",
        AsyncMock(
            return_value=ReviewerModels(choice, choice, False),
        ),
    )
    graph = RemoteGraph("reviewer", forbidden_plan)
    run = RemoteRun(
        thread_id="visual-thread",
        assistant_id="reviewer",
        configurable={
            "frontend_review_id": str(uuid7()),
            "head_sha": "b" * 40,
            "repo": {"owner": "acme", "name": "widgets"},
            "pr_number": 7,
        },
    )
    started = await graph.start_run(run)
    assert started.catalog.operations == []
    assert started.catalog.sandbox is None
    assert started.catalog.loop is not None and started.catalog.loop.at_end == []
    assert started.catalog.code_interpreter is False
    assert started.catalog.frontend_review_description is None
    assert await graph.api(run, PREPARE.name, Prepare().model_dump(mode="json")) == {
        "update": {},
        "messages": [],
        "jump_to": None,
        "events": [],
    }
