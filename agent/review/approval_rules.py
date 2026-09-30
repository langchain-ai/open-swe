"""Compile trusted approval policies into a deliberately small, non-executable rule set."""

import asyncio
import hashlib
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from agent.dashboard.workspace_settings_cache import cached_workspace_settings
from agent.prompts import prompt
from agent.review.approvals import fetch_approvals_md
from agent.store import TypedStore, now_iso
from agent.utils.model import make_model, provider_model_kwargs

COMPILER_VERSION = 2


class DocumentationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["markdown_documentation"] = "markdown_documentation"
    paths: list[str] = Field(max_length=30)
    prefixes: list[str] = Field(max_length=30)
    max_changed_lines: int = Field(ge=1, le=1000)
    policy_excerpt: str = Field(min_length=1, max_length=2000)

    @field_validator("paths", "prefixes")
    @classmethod
    def safe_paths(cls, paths: list[str], info: ValidationInfo) -> list[str]:
        for path in paths:
            prefix = info.field_name == "prefixes"
            if (
                not (path.startswith("docs/") or (not prefix and path == "README.md"))
                or any(
                    not part or part.startswith(".") for part in path.removesuffix("/").split("/")
                )
                or any(char in path for char in "*?[]\\")
                or any(ord(char) < 32 or ord(char) == 127 for char in path)
                or (prefix and not path.endswith("/"))
                or (not prefix and not path.endswith(".md"))
            ):
                raise ValueError("Rules may only match Markdown under docs/ or README.md")
        return paths


class CompiledRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[DocumentationRule] = Field(max_length=10)
    explanation: str = Field(max_length=2000)


class ApprovalProgram(CompiledRules):
    policy_hash: str
    compiler_version: int = COMPILER_VERSION
    generated_at: str


PROGRAMS = TypedStore(("review_approval_programs",), ApprovalProgram)


def policy_hash(policy: str) -> str:
    return hashlib.sha256(policy.encode()).hexdigest()


async def cached_approval_program(owner: str, repo: str, policy: str) -> ApprovalProgram | None:
    """Read a previously generated policy program without invoking a model."""
    return await PROGRAMS.get(f"{owner}/{repo}:{COMPILER_VERSION}:{policy_hash(policy)}")


async def approval_program(
    owner: str,
    repo: str,
    policy: str,
    *,
    refresh: bool = False,
    workspace: str | None = None,
) -> ApprovalProgram:
    """Generate once per policy version, never from a candidate pull request's contents."""
    digest = policy_hash(policy)
    key = f"{owner}/{repo}:{COMPILER_VERSION}:{digest}"
    if not refresh and (cached := await PROGRAMS.get(key)) is not None:
        return cached
    settings = await cached_workspace_settings(workspace)
    model_id, effort = settings.default_model("reviewer")
    model = make_model(
        model_id,
        use_gateway=settings.effective_gateway_enabled,
        **provider_model_kwargs(model_id, effort, max_tokens=4000),
    )
    async with asyncio.timeout(60):
        result = await model.with_structured_output(CompiledRules).ainvoke(
            [
                SystemMessage(content=prompt("reviewer/compile-approvals")),
                HumanMessage(content=policy),
            ],
            config={"callbacks": [], "run_name": "compile-approval-policy"},
        )
    if not isinstance(result, CompiledRules):
        raise ValueError("Approval compiler did not return validated rules")
    if any(
        rule.policy_excerpt not in policy
        or any(path not in policy for path in [*rule.paths, *rule.prefixes])
        for rule in result.rules
    ):
        raise ValueError("Approval rule does not cite paths in the trusted policy")
    program = ApprovalProgram(**result.model_dump(), policy_hash=digest, generated_at=now_iso())
    return await PROGRAMS.put(key, program)


async def refresh_approval_program(owner: str, repo: str, *, token: str) -> ApprovalProgram:
    """Manually rebuild from the default branch, never a caller-supplied PR ref."""
    policy = await fetch_approvals_md(owner, repo, None, token=token)
    if policy is None:
        raise ValueError("No readable .open-swe/APPROVALS.md on the default branch")
    return await approval_program(owner, repo, policy, refresh=True)
