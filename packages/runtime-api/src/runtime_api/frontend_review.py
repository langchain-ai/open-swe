"""A PR-bound anonymous visual capture, answered by Open SWE's evidence service."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from runtime_api.endpoint import Endpoint
from runtime_api.operations import RunRequest


class FrontendReviewArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(max_length=2000)
    head_url: str = Field(max_length=2000)
    paths: list[str] = Field(default_factory=lambda: ["/"], min_length=1, max_length=5)
    auth_mode: Literal["dummy", "none"] = "dummy"


class CaptureFrontendReview(RunRequest):
    request: FrontendReviewArguments


class FrontendPageResult(BaseModel):
    id: str
    path: str
    width: int
    height: int
    changed_pixels: int
    total_pixels: int
    changed_ratio: float


class FrontendScreenshot(BaseModel):
    scenario_id: str
    role: Literal["before", "after"]
    width: int
    height: int
    png_base64: str = Field(max_length=2_500_000)


class CapturedFrontendReview(BaseModel):
    review_id: str
    status: Literal["pending", "running", "complete", "blocked", "failed"]
    report_url: str
    error: str | None = None
    summary: str | None = None
    build_verification: Literal["unverified", "verified"] = "unverified"
    ai_review_status: Literal["not_run", "running", "complete", "failed"] = "not_run"
    pages: list[FrontendPageResult] = Field(default_factory=list)
    screenshots: list[FrontendScreenshot] = Field(default_factory=list, max_length=10)


class ReadFrontendReviewArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: UUID


class ReadFrontendReview(RunRequest):
    request: ReadFrontendReviewArguments


class FrontendAnnotation(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    title: str = Field(max_length=200)
    body: str = Field(max_length=3000)


class FrontendAssessmentArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: UUID
    summary: str = Field(min_length=1, max_length=12000)
    annotations_by_scenario: dict[str, list[FrontendAnnotation]] = Field(default_factory=dict)


class SubmitFrontendReview(RunRequest):
    request: FrontendAssessmentArguments


class SubmittedFrontendReview(BaseModel):
    review_id: UUID
    report_url: str
    ai_review_status: Literal["complete"] = "complete"


CAPTURE_FRONTEND_REVIEW = Endpoint(
    "capture_frontend_review", CaptureFrontendReview, CapturedFrontendReview, seconds=900
)

SUBMIT_FRONTEND_REVIEW = Endpoint(
    "submit_frontend_review", SubmitFrontendReview, SubmittedFrontendReview
)

READ_FRONTEND_REVIEW = Endpoint("read_frontend_review", ReadFrontendReview, CapturedFrontendReview)
