"""A PR-bound anonymous visual capture, answered by Open SWE's evidence service."""

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from runtime_api.endpoint import Endpoint
from runtime_api.operations import RunRequest


class FrontendInteractionStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["hover", "press", "click", "focus", "type", "escape"]
    selector: str = Field(min_length=1, max_length=300)
    text: str | None = Field(default=None, max_length=200)
    checkpoint: str | None = Field(default=None, min_length=1, max_length=100)
    expect_visible: str | None = Field(default=None, min_length=1, max_length=300)

    @model_validator(mode="after")
    def typed_text(self) -> Self:
        if (self.action == "type") != (self.text is not None):
            raise ValueError("Only type steps require synthetic text")
        if self.action == "press" and self.checkpoint is None:
            raise ValueError("A press step must capture its transient active state")
        return self


class FrontendJourney(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=500)
    steps: list[FrontendInteractionStep] = Field(min_length=1, max_length=8)


class FrontendReviewArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(max_length=2000)
    head_url: str = Field(max_length=2000)
    paths: list[str] = Field(default_factory=lambda: ["/"], min_length=1, max_length=5)
    auth_mode: Literal["dummy", "none"] = "dummy"
    journeys: list[FrontendJourney] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def bounded_journeys(self) -> Self:
        if any(journey.path not in self.paths for journey in self.journeys):
            raise ValueError("Interaction journeys must use requested routes")
        checkpoints = sum(step.checkpoint is not None for j in self.journeys for step in j.steps)
        if self.journeys and not all(any(s.checkpoint for s in j.steps) for j in self.journeys):
            raise ValueError("Each interaction journey needs a checkpoint")
        if checkpoints > 5:
            raise ValueError("Choose at most five meaningful interaction checkpoints per review")
        return self


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


class FrontendInteractionEvidence(BaseModel):
    scenario_id: str
    title: str
    status: Literal["complete", "blocked", "failed"]
    actions: list[str]
    before_error: str | None = None
    after_error: str | None = None
    layout_warnings: list[str] = Field(default_factory=list)


class CapturedFrontendReview(BaseModel):
    review_id: str
    status: Literal["pending", "running", "complete", "blocked", "failed"]
    report_url: str
    error: str | None = None
    summary: str | None = None
    build_verification: Literal["unverified", "verified"] = "unverified"
    ai_review_status: Literal["not_run", "running", "complete", "failed"] = "not_run"
    pages: list[FrontendPageResult] = Field(default_factory=list)
    interactions: list[FrontendInteractionEvidence] = Field(default_factory=list)
    screenshots: list[FrontendScreenshot] = Field(default_factory=list, max_length=20)


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
