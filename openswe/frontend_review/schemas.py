import re
from datetime import datetime
from typing import Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RunStatus = Literal["pending", "running", "complete", "blocked", "failed"]
AuthMode = Literal["dummy", "none"]
MediaType = Literal["image/png", "video/webm", "video/mp4"]
ASSET_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}")
MEDIA_FIELDS = (
    "before_url",
    "after_url",
    "diff_url",
    "mask_url",
    "before_video_url",
    "after_video_url",
)


class StartFrontendReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(max_length=2000)
    head_url: str = Field(max_length=2000)
    paths: list[str] = Field(default_factory=lambda: ["/"], min_length=1, max_length=5)
    auth_mode: AuthMode = "dummy"

    @field_validator("base_url", "head_url")
    @classmethod
    def public_https_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Preview URLs must use HTTPS without credentials, query, or fragment")
        return value.rstrip("/")

    @field_validator("paths")
    @classmethod
    def route_paths(cls, paths: list[str]) -> list[str]:
        for path in paths:
            parsed = urlsplit(path)
            if (
                not path.startswith("/")
                or path.startswith("//")
                or "\\" in path
                or parsed.query
                or parsed.fragment
                or len(path) > 500
            ):
                raise ValueError("Capture paths must be local routes without query or fragment")
        if len(set(paths)) != len(paths):
            raise ValueError("Capture paths must be unique")
        return paths


class Rectangle(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class ChangedRegion(Rectangle):
    pixels: int = Field(ge=0)


class ReviewAnnotation(Rectangle):
    title: str = Field(max_length=200)
    body: str = Field(max_length=3000)


class DifferenceMetrics(BaseModel):
    changed_pixels: int = Field(ge=0)
    total_pixels: int = Field(gt=0)
    changed_ratio: float = Field(ge=0, le=1)
    threshold: int = Field(default=18, ge=0, le=255)

    @model_validator(mode="after")
    def consistent_counts(self) -> Self:
        if self.changed_pixels > self.total_pixels:
            raise ValueError("Changed pixels exceed the image area")
        if abs(self.changed_ratio - self.changed_pixels / self.total_pixels) > 0.000001:
            raise ValueError("Changed ratio does not match the pixel counts")
        return self


class FrontendScenario(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
    title: str = Field(max_length=200)
    path: str = Field(max_length=500)
    status: Literal["complete", "blocked", "failed"]
    before_url: str | None = None
    after_url: str | None = None
    diff_url: str | None = None
    mask_url: str | None = None
    before_video_url: str | None = None
    after_video_url: str | None = None
    width: int | None = Field(default=None, gt=0, le=3840)
    height: int | None = Field(default=None, gt=0, le=2160)
    metrics: DifferenceMetrics | None = None
    regions: list[ChangedRegion] = Field(default_factory=list, max_length=100)
    annotations: list[ReviewAnnotation] = Field(default_factory=list, max_length=100)
    mask_rects: list[Rectangle] = Field(default_factory=list, max_length=100)
    tile_size: int = Field(default=32, ge=1, le=256)
    min_region_pixels: int = Field(default=4, ge=1)
    error: str | None = Field(default=None, max_length=3000)


class FrontendReport(BaseModel):
    summary: str = Field(max_length=12000)
    ai_review_status: Literal["not_run", "running", "complete", "failed"] = "not_run"
    assessment_thread_id: str | None = Field(default=None, max_length=200)
    assessment_error: str | None = Field(default=None, max_length=3000)
    build_verification: Literal["unverified", "verified"] = "unverified"
    video_status: Literal["unavailable", "recorded"] = "unavailable"
    scenarios: list[FrontendScenario] = Field(default_factory=list, max_length=5)


class FrontendReviewView(BaseModel):
    id: UUID
    status: RunStatus
    auth_mode: AuthMode
    base_url: str
    head_url: str
    paths: list[str]
    base_sha: str
    head_sha: str
    created_at: datetime
    updated_at: datetime
    error: str | None
    report: FrontendReport | None


class FrontendReviewList(BaseModel):
    runs: list[FrontendReviewView]
