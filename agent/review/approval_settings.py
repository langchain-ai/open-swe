"""App-managed approval requirements, immutable revisions, and effective policies."""

import hashlib
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from pydantic import Field, field_validator
from sqlalchemy import text

from agent import database
from agent.prompts import load_prompt
from agent.review.approval import (
    PolicyCriterion,
    PolicyRules,
    PolicySnapshot,
    StrictModel,
    parse_policy,
)
from agent.store import TypedStore, now_iso


class PolicyDefinition(StrictModel):
    rules: PolicyRules
    criteria_markdown: str = Field(max_length=24_000)

    @field_validator("rules")
    @classmethod
    def bounded_rules(cls, rules: PolicyRules) -> PolicyRules:
        if len(rules.required_checks) > 100 or len(rules.human_review_paths) > 100:
            raise ValueError("Each policy may contain at most 100 check names or path patterns")
        if any(len(entry) > 500 for entry in [*rules.required_checks, *rules.human_review_paths]):
            raise ValueError("Check names and path patterns must fit within 500 characters")
        return rules

    @field_validator("criteria_markdown")
    @classmethod
    def valid_criteria(cls, value: str) -> str:
        if value:
            if value.lstrip("\ufeff").startswith(("+++", "---")):
                raise ValueError(
                    "Use the policy fields for rules; criteria must not have frontmatter"
                )
            parsed = parse_policy(value, source="settings", base_sha="", head_sha="")
            if any(len(c.id) > 128 or len(c.requirement) > 20_000 for c in parsed.criteria):
                raise ValueError("Policy criteria need shorter names or requirements")
        return value


class PolicyRevision(StrictModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    repository: str | None
    policy: PolicyDefinition | None
    updated_by: str
    updated_at: str = Field(default_factory=now_iso)


class PolicySettingsUpdate(StrictModel):
    policy: PolicyDefinition | None
    expected_version: str = Field(min_length=1, max_length=64)


class PolicySettingsView(StrictModel):
    repository: str | None
    policy: PolicyDefinition | None
    shared_policy: PolicyDefinition
    effective_rules: PolicyRules
    effective_version: str
    revision: str | None
    updated_by: str | None
    updated_at: str | None
    can_edit: bool = False


class PolicyConflict(ValueError):
    pass


CURRENT = TypedStore(["review_approval_policies"], PolicyRevision)
REVISIONS = TypedStore(["review_approval_policy_revisions"], PolicyRevision)


def normalize_repository(repository: str | None) -> str | None:
    if repository is None:
        return None
    normalized = repository.strip().lower()
    if not re.fullmatch(
        r"[a-z0-9][a-z0-9-]{0,38}/[a-z0-9_.-]{1,100}", normalized
    ) or normalized.rsplit("/", 1)[-1] in {".", ".."}:
        raise ValueError("Repository must be owner/repo")
    return normalized


def _builtin_policy() -> PolicyDefinition:
    parsed = parse_policy(
        load_prompt("reviewer/approval-policy.md"), source="default", base_sha="", head_sha=""
    )
    return PolicyDefinition(
        rules=parsed.rules,
        criteria_markdown="\n\n".join(f"## {c.title}\n{c.requirement}" for c in parsed.criteria),
    )


def _combined_rules(shared: PolicyRules, local: PolicyRules | None) -> PolicyRules:
    if local is None:
        return shared.model_copy(deep=True)
    confidence_rank = {"low": 0, "medium": 1, "high": 2}
    return PolicyRules(
        max_risk_score=min(shared.max_risk_score, local.max_risk_score),
        minimum_confidence=max(
            [shared.minimum_confidence, local.minimum_confidence], key=confidence_rank.__getitem__
        ),
        required_checks=list(dict.fromkeys([*shared.required_checks, *local.required_checks])),
        human_review_paths=list(
            dict.fromkeys([*shared.human_review_paths, *local.human_review_paths])
        ),
    )


def _criteria(definition: PolicyDefinition, scope: str) -> list[PolicyCriterion]:
    if not definition.criteria_markdown:
        return []
    parsed = parse_policy(definition.criteria_markdown, source="settings", base_sha="", head_sha="")
    return [
        criterion.model_copy(update={"id": f"{scope}:{criterion.id}"})
        for criterion in parsed.criteria
    ]


async def _resolved_policy(repository: str | None) -> tuple[PolicySettingsView, PolicySnapshot]:
    repository = normalize_repository(repository)
    shared_record = await CURRENT.get("default")
    repo_record = await CURRENT.get(repository) if repository else None
    shared = shared_record.policy if shared_record and shared_record.policy else _builtin_policy()
    local = repo_record.policy if repo_record else None
    rules = _combined_rules(shared.rules, local.rules if local else None)
    criteria = _criteria(shared, "shared") + (_criteria(local, "repository") if local else [])
    if not criteria:
        raise ValueError("Shared approval policy must contain criteria")
    revisions = {"shared": shared_record.id if shared_record else "builtin"}
    if repository:
        revisions[repository] = repo_record.id if repo_record else "inherited"
    content = "# Shared approval policy\n\n" + shared.criteria_markdown
    if local and local.criteria_markdown:
        content += "\n\n# Repository requirements\n\n" + local.criteria_markdown
    version = hashlib.sha256(
        json.dumps(
            {
                "rules": rules.model_dump(),
                "criteria": [c.model_dump() for c in criteria],
                "revisions": revisions,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    snapshot = PolicySnapshot(
        source="repository_settings"
        if local
        else "settings"
        if shared_record and shared_record.policy
        else "default",
        version=version,
        base_sha="",
        head_sha="",
        content=content,
        rules=rules,
        criteria=criteria,
        settings_revisions=revisions,
    )
    current = repo_record if repository else shared_record
    return PolicySettingsView(
        repository=repository,
        policy=current.policy if current else None,
        shared_policy=shared,
        effective_rules=rules,
        effective_version=version,
        revision=current.id if current else None,
        updated_by=current.updated_by if current else None,
        updated_at=current.updated_at if current else None,
    ), snapshot


async def get_policy_settings(repository: str | None = None) -> PolicySettingsView:
    view, _ = await _resolved_policy(repository)
    return view


async def load_policy_snapshot(repository: str, *, base_sha: str, head_sha: str) -> PolicySnapshot:
    _, snapshot = await _resolved_policy(repository)
    return snapshot.model_copy(update={"base_sha": base_sha, "head_sha": head_sha})


@asynccontextmanager
async def _policy_lock() -> AsyncIterator[None]:
    # Shared defaults affect every repository, so all policy writes use the same lock.
    async with database.transaction() as conn:
        await conn.execute(text("SET LOCAL lock_timeout = '5s'"))
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:subject, 0))"),
            {"subject": "open-swe:review-approval-policy"},
        )
        yield


async def save_policy_settings(
    repository: str | None, *, policy: PolicyDefinition | None, expected_version: str, login: str
) -> PolicySettingsView:
    repository = normalize_repository(repository)
    if repository is None and policy is not None and not policy.criteria_markdown:
        raise ValueError("Shared approval policy must contain criteria")
    async with _policy_lock():
        current = await get_policy_settings(repository)
        if current.effective_version != expected_version:
            raise PolicyConflict("Approval policy changed. Reload the policy before saving.")
        # Validate merged list limits before writing either record.
        if repository and policy:
            _combined_rules(current.shared_policy.rules, policy.rules)
        revision = PolicyRevision(repository=repository, policy=policy, updated_by=login)
        await REVISIONS.put(revision.id, revision)
        await CURRENT.put(repository or "default", revision)
        return await get_policy_settings(repository)
