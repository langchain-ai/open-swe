"""The frontend review tool calls one typed, PR-bound backend operation."""

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from pydantic import JsonValue
from runtime_api.endpoint import call
from runtime_api.frontend_review import (
    CAPTURE_FRONTEND_REVIEW,
    READ_FRONTEND_REVIEW,
    SUBMIT_FRONTEND_REVIEW,
    CapturedFrontendReview,
    CaptureFrontendReview,
    FrontendAssessmentArguments,
    FrontendJourney,
    FrontendReviewArguments,
    ReadFrontendReview,
    ReadFrontendReviewArguments,
    SubmitFrontendReview,
)

from rt.backend import OpenSweBackend
from rt.tools import run_id


def _content(result: CapturedFrontendReview) -> list[dict[str, JsonValue]]:
    content: list[dict[str, JsonValue]] = [
        {"type": "text", "text": result.model_dump_json(exclude={"screenshots"})}
    ]
    for screenshot in result.screenshots:
        content.extend(
            [
                {
                    "type": "text",
                    "text": (
                        f"Scenario {screenshot.scenario_id}: {screenshot.role} screenshot, "
                        f"displayed at {screenshot.width}x{screenshot.height}. "
                        "Use original page dimensions for annotations."
                    ),
                },
                {"type": "image", "url": f"data:image/png;base64,{screenshot.png_base64}"},
            ]
        )
    return content


def frontend_review_tool(backend: OpenSweBackend, description: str) -> StructuredTool:
    async def capture(
        base_url: str,
        head_url: str,
        paths: list[str] | None = None,
        auth_mode: str = "dummy",
        journeys: list[FrontendJourney] | None = None,
    ) -> list[dict[str, JsonValue]]:
        arguments = FrontendReviewArguments.model_validate(
            {
                "base_url": base_url,
                "head_url": head_url,
                "paths": paths or ["/"],
                "auth_mode": auth_mode,
                "journeys": journeys or [],
            }
        )
        result = await call(
            backend,
            CAPTURE_FRONTEND_REVIEW,
            CaptureFrontendReview(run_id=run_id(), request=arguments),
        )
        return _content(result)

    return StructuredTool.from_function(
        coroutine=capture,
        name="review_frontend",
        description=description,
        args_schema=FrontendReviewArguments,
    )


def frontend_inspect_tool(backend: OpenSweBackend, description: str) -> StructuredTool:
    async def inspect(**arguments: JsonValue) -> list[dict[str, JsonValue]]:
        request = ReadFrontendReviewArguments.model_validate(arguments)
        result = await call(
            backend,
            READ_FRONTEND_REVIEW,
            ReadFrontendReview(run_id=run_id(), request=request),
        )
        return _content(result)

    return StructuredTool.from_function(
        coroutine=inspect,
        name="read_frontend_review",
        description=description,
        args_schema=ReadFrontendReviewArguments,
    )


def frontend_assessment_tool(backend: OpenSweBackend, description: str) -> StructuredTool:
    async def submit(runtime: ToolRuntime, **arguments: JsonValue) -> Command:
        request = FrontendAssessmentArguments.model_validate(arguments)
        result = await call(
            backend,
            SUBMIT_FRONTEND_REVIEW,
            SubmitFrontendReview(run_id=run_id(), request=request),
        )
        return Command(
            update={
                "open_swe_state": {"frontend_review_complete": True},
                "messages": [
                    ToolMessage(
                        content=result.model_dump_json(),
                        tool_call_id=runtime.tool_call_id or "",
                        name="submit_frontend_review",
                    )
                ],
            }
        )

    return StructuredTool.from_function(
        coroutine=submit,
        name="submit_frontend_review",
        description=description,
        args_schema=FrontendAssessmentArguments.model_json_schema(),
    )
