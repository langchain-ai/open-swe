import asyncio
import base64
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from io import BytesIO
from unittest.mock import AsyncMock, Mock
from uuid import uuid7

import httpx2
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


async def test_anonymous_capture_finishes_blocked_before_provider_access_or_browser_network(
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
        auth_mode="none",
    )
    run.pull_request = PullRequest(owner="acme", repo="widgets", number=7)
    finish = AsyncMock()
    monkeypatch.setattr(capture, "finish_review", finish)
    provider = Mock(side_effect=AssertionError("must not access browser service credentials"))
    monkeypatch.setattr(capture.SandboxAPI, "from_env", provider)

    await capture.capture_run(run)

    assert not provider.called
    assert finish.call_args.kwargs["status"] == "blocked"
    assert "browser service enforces network isolation" in finish.call_args.kwargs["error"]
    assert finish.call_args.kwargs["assets"] == []


@pytest.mark.parametrize(
    "http_status,late_status,expected_status,base_origin,rendered_base",
    [
        (200, None, "complete", "https://base.vercel.app", "https://base.vercel.app/"),
        (401, None, "blocked", "https://base.vercel.app", "https://base.vercel.app/"),
        (403, None, "blocked", "https://base.vercel.app", "https://base.vercel.app/"),
        (503, None, "failed", "https://base.vercel.app", "https://base.vercel.app/"),
        (None, None, "failed", "https://base.vercel.app", "https://base.vercel.app/"),
        (200, 503, "failed", "https://base.vercel.app", "https://base.vercel.app/"),
        (200, None, "complete", "https://base.vercel.app:443", "https://base.vercel.app/"),
        (200, None, "complete", "https://Base.vercel.app", "https://base.vercel.app/"),
        (200, None, "blocked", "https://base.vercel.app", "http://base.vercel.app/"),
    ],
)
async def test_capture_uses_main_document_http_status_instead_of_rendered_error_pages(
    monkeypatch: pytest.MonkeyPatch,
    http_status: int | None,
    late_status: int | None,
    expected_status: str,
    base_origin: str,
    rendered_base: str,
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
                if location == f"{base_origin}/":
                    location = rendered_base
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
        base_url=base_origin,
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


@pytest.mark.parametrize("lost_create_response", [True, False])
async def test_disposable_browser_cleanup_handles_ambiguous_creation_and_existing_browser(
    monkeypatch: pytest.MonkeyPatch,
    lost_create_response: bool,
) -> None:
    resources = set() if lost_create_response else {"disposable"}

    async def request(
        self: capture.SandboxAPI,
        method: str,
        path: str,
        *,
        json: dict[str, object] | None = None,
        timeout: float,
    ) -> httpx2.Response:
        status = 200
        body: dict[str, object] = {}
        if method == "POST" and path == "/browsers":
            if lost_create_response:
                resources.add("disposable")
                raise TimeoutError("Browser created but response was lost")
            status = 409
        elif method == "GET":
            body = {"id": "browser-id", "name": "disposable", "status": "running"}
        elif method == "POST":
            body = {"id": "window-id", "api_cdp_url": "https://untrusted.example.test/cdp"}
        elif method == "DELETE":
            resources.remove("disposable")
        response = httpx2.Response(
            status,
            json=body,
            request=httpx2.Request(method, f"https://browser.example.test{path}"),
        )
        response.raise_for_status()
        return response

    monkeypatch.setattr(capture, "_require_browser_network_isolation", lambda: None)
    monkeypatch.setattr(
        capture.SandboxAPI,
        "from_env",
        lambda: capture.SandboxAPI("https://browser.example.test", None),
    )
    monkeypatch.setattr(capture.SandboxAPI, "request", request)
    failure = TimeoutError if lost_create_response else RuntimeError
    with pytest.raises(failure):
        async with capture._browser("disposable"):
            pytest.fail("Untrusted synthetic browser must not connect")

    assert resources == set()


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


async def test_interaction_replay_captures_pressed_before_release_and_keeps_missing_states_explicit() -> (
    None
):
    from runtime_api.frontend_review import FrontendInteractionStep

    from openswe.frontend_review.interactions import InteractionReplay

    class Browser:
        def __init__(self) -> None:
            self.pressed = False
            self.open = False
            self.text = ""
            self.frames: list[tuple[bool, bool, str]] = []
            self.clicks = 0

        async def evaluate(self, expression: str, session: str) -> JsonValue:
            if expression == "location.href":
                return "https://head.vercel.app/"
            if expression.startswith("Boolean("):
                return False
            if expression.startswith("(selector =>"):
                if '"#missing"' in expression:
                    return None
                if "const dialog" in expression:
                    return (
                        ["Dialog bounds extend beyond the captured viewport"] if self.open else []
                    )
                return {"x": 40, "y": 30}
            return True

        async def call(
            self,
            method: str,
            params: dict[str, JsonValue] | None = None,
            *,
            session: str | None = None,
        ) -> dict[str, JsonValue]:
            params = params or {}
            if method == "Input.dispatchMouseEvent":
                if params["type"] == "mousePressed":
                    self.pressed = True
                elif params["type"] == "mouseReleased":
                    if self.pressed and params["x"] == 40:
                        self.clicks += 1
                        self.open = True
                    self.pressed = False
            elif method == "Input.insertText":
                assert isinstance(params["text"], str)
                self.text = params["text"]
            elif method == "Page.captureScreenshot":
                self.frames.append((self.pressed, self.open, self.text))
                return {"data": base64.b64encode(_png(Image.new("RGB", (2, 1)))).decode()}
            return {}

    browser = Browser()
    states = await InteractionReplay(browser, "session", "https://head.vercel.app").capture(
        [
            FrontendInteractionStep(action="hover", selector="#open", checkpoint="Hover"),
            FrontendInteractionStep(action="press", selector="#open", checkpoint="Pressed"),
            FrontendInteractionStep(action="click", selector="#open"),
            FrontendInteractionStep(
                action="type",
                selector="#name",
                text="Design QA example",
                checkpoint="Dialog filled",
                expect_visible="#dialog",
            ),
            FrontendInteractionStep(
                action="click", selector="#missing", checkpoint="Missing control"
            ),
            FrontendInteractionStep(action="focus", selector="#name", checkpoint="Dependent state"),
        ]
    )
    assert browser.frames == [
        (False, False, ""),
        (True, False, ""),
        (False, True, "Design QA example"),
    ]
    assert browser.clicks == 1  # Releasing the pressed-state capture must not activate the button.
    assert states[2].warnings == ["Dialog bounds extend beyond the captured viewport"]
    assert states[3].image is None and "missing" in (states[3].error or "")
    assert states[4].image is None and "Earlier step" in (states[4].error or "")


async def test_interaction_capture_keeps_route_pair_and_pr_only_state_when_control_is_new(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime_api.frontend_review import FrontendJourney

    from openswe.frontend_review.interactions import InteractionCapture
    from openswe.frontend_review.service import _validate_report

    png = _png(Image.new("RGB", (2, 1)))

    class Browser:
        async def screenshot(self, url: str) -> bytes:
            return png

        async def interact(self, url: str, journey: FrontendJourney) -> list[InteractionCapture]:
            return (
                [InteractionCapture(image=png)]
                if "head" in url
                else [InteractionCapture(error="New control missing on base")]
            )

    @asynccontextmanager
    async def browser(_name: str) -> AsyncIterator[Browser]:
        yield Browser()

    run = FrontendReviewRun(
        id=uuid7(),
        pull_request_id=uuid7(),
        base_sha="a" * 40,
        head_sha="b" * 40,
        base_url="https://base.vercel.app",
        head_url="https://head.vercel.app",
        paths=["/"],
        auth_mode="none",
        journeys=[
            {
                "title": "New dialog",
                "path": "/",
                "steps": [{"action": "click", "selector": "#open", "checkpoint": "Dialog open"}],
            }
        ],
    )
    run.pull_request = PullRequest(owner="acme", repo="widgets", number=7)
    finish = AsyncMock()
    monkeypatch.setattr(capture, "_browser", browser)
    monkeypatch.setattr(capture, "finish_review", finish)
    await capture.capture_run(run)
    report = finish.call_args.kwargs["report"]
    assert finish.call_args.kwargs["status"] == "complete"
    assert report.scenarios[0].status == "complete"
    state = report.scenarios[1]
    assert state.interaction and state.status == "blocked" and state.metrics is None
    assert state.before_url is None and state.after_url is not None
    assert state.before_error == "New control missing on base"
    _validate_report(report, finish.call_args.kwargs["assets"], complete=True)


@pytest.mark.parametrize("outside_route", [False, True])
def test_capture_rejects_excess_or_out_of_scope_interaction_work(outside_route: bool) -> None:
    from pydantic import ValidationError

    from openswe.frontend_review.schemas import StartFrontendReview

    step = {"action": "hover", "selector": "#changed", "checkpoint": "Hover"}
    with pytest.raises(
        ValidationError, match="requested routes" if outside_route else "at most five"
    ):
        StartFrontendReview.model_validate(
            {
                "base_url": "https://base.vercel.app",
                "head_url": "https://head.vercel.app",
                "paths": ["/projects"],
                "journeys": [
                    {
                        "title": "Changed control",
                        "path": "/settings" if outside_route else "/projects",
                        "steps": [step] if outside_route else [step] * 6,
                    }
                ],
            }
        )
