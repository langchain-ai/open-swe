"""Typed settings, GitHub inputs, and durable docs run records."""

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent.mcp.models import MCPConnectionUpdate
from agent.store import TypedStore


def repository_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value) or any(
        part in {".", ".."} for part in value.split("/")
    ):
        raise ValueError("Use owner/repository")
    return value.lower()


class DocsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    docs_repository: str = ""
    docs_base_branch: str = "main"
    docs_mcp_url: str = ""
    source_repositories: list[str] = Field(default_factory=list, max_length=500)
    revision: str = ""

    @field_validator("docs_repository")
    @classmethod
    def validate_docs_repo(cls, value: str) -> str:
        return repository_name(value) if value else ""

    @field_validator("source_repositories")
    @classmethod
    def validate_sources(cls, values: list[str]) -> list[str]:
        return sorted({repository_name(value) for value in values})

    @field_validator("docs_base_branch")
    @classmethod
    def validate_branch(cls, value: str) -> str:
        if (
            not value
            or len(value) > 200
            or re.search(r"[\s~^:?*\[\\]", value)
            or any(part in value for part in ("..", "@{", "//"))
            or value.startswith(("/", "-"))
            or value.endswith(("/", ".", ".lock"))
        ):
            raise ValueError("Use a valid Git branch name")
        return value

    @field_validator("docs_mcp_url")
    @classmethod
    def validate_mcp(cls, value: str) -> str:
        return MCPConnectionUpdate(name="docs", url=value).url if value else ""

    @model_validator(mode="after")
    def validate_enabled(self) -> DocsSettings:
        if self.enabled and not self.docs_repository:
            raise ValueError("Choose a docs repository before enabling Open SWE Docs")
        if self.docs_repository in self.source_repositories:
            raise ValueError("The docs repository cannot trigger its own docs runs")
        return self


class Repository(BaseModel):
    full_name: str
    private: bool = True


class PRRef(BaseModel):
    sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    ref: str
    repo: Repository | None = None


class Label(BaseModel):
    name: str


class PRUser(BaseModel):
    login: str = ""


class PullRequest(BaseModel):
    number: int = Field(gt=0)
    state: str
    draft: bool = False
    title: str
    body: str | None = None
    html_url: str
    head: PRRef
    base: PRRef
    labels: list[Label] = Field(default_factory=list)
    user: PRUser | None = None


class LinkedPR(BaseModel):
    number: int
    sha: str
    base_sha: str
    url: str
    state: str
    draft: bool


class DocsSnapshot(BaseModel):
    source_repository: str
    source: PullRequest
    settings: DocsSettings
    docs_base_sha: str
    links: list[LinkedPR] = Field(default_factory=list)
    code_review_enabled: bool = False
    docs_enabled: bool = True

    @property
    def key(self) -> str:
        return f"{self.source_repository}#{self.source.number}"

    @property
    def fingerprint(self) -> str:
        # Agent comments and generated links must not create an endless review loop.
        value = {
            "repository": self.source_repository,
            "number": self.source.number,
            "head": self.source.head.sha,
            "base": self.source.base.sha,
            "code_review_enabled": self.code_review_enabled,
            "docs_enabled": self.docs_enabled,
            "body": self.source.body,
            "title": self.source.title,
            "settings": self.settings.revision,
            "docs_base": self.docs_base_sha,
            "links": [link.model_dump() for link in self.links],
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class DocsJob(BaseModel):
    snapshot: DocsSnapshot
    thread_id: str
    run_id: str = ""
    status: Literal["pending", "running", "completed", "skipped", "failed"] = "pending"
    result: str = ""
    docs_pr_url: str = ""
    check_id: int | None = None


SETTINGS = TypedStore(["docs_settings"], DocsSettings)
JOBS = TypedStore(["docs_jobs"], DocsJob)


async def settings() -> DocsSettings:
    return await SETTINGS.get("default") or DocsSettings()


def eligible(config: DocsSettings, repository: str, pr: PullRequest) -> bool:
    return (
        config.enabled
        and repository.lower() in config.source_repositories
        and pr.state == "open"
        and not pr.draft
        and all(label.name.lower() != "skip-docs" for label in pr.labels)
    )
