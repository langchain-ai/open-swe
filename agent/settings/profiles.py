"""User profile schema and LangGraph Store CRUD.

Only the user-editable settings (model, effort, default_repo, …) live here, in
``["profiles"]``. The user's GitHub OAuth tokens are a separate namespace owned
by :mod:`agent.settings.github_tokens`, so a profile save and an OAuth
callback can interleave without clobbering each other's fields.
"""

from typing import Any

from pydantic import BaseModel, model_validator

from ..store import get_value, now_iso, put_value, search_values
from .options import SUPPORTED_MODEL_IDS, model_supports_effort, provider_fallback_pair

PROFILES_NAMESPACE: list[str] = ["profiles"]


class ProfileUpdate(BaseModel):
    default_model: str
    reasoning_effort: str
    default_subagent_model: str | None = None
    subagent_reasoning_effort: str | None = None
    default_repo: str | None = None
    base_branch: str | None = None
    branch_prefix: str | None = None
    auto_fix_ci: bool = True
    draft_prs: bool | None = None
    review_draft_prs: bool | None = None

    @model_validator(mode="after")
    def _normalize_stale_model_pairs(self) -> "ProfileUpdate":
        model, effort = _normalize_stale_model_pair(
            self.default_model,
            self.reasoning_effort,
        )
        self.default_model = model
        if effort is not None:
            self.reasoning_effort = effort
        if self.default_subagent_model is not None:
            self.default_subagent_model, self.subagent_reasoning_effort = (
                _normalize_stale_model_pair(
                    self.default_subagent_model,
                    self.subagent_reasoning_effort,
                )
            )
        return self

    def validate_pairing(self) -> None:
        if not model_supports_effort(self.default_model, self.reasoning_effort):
            raise ValueError(
                f"effort {self.reasoning_effort!r} not supported by {self.default_model!r}"
            )
        if self.default_subagent_model is None and self.subagent_reasoning_effort is None:
            return
        if self.default_subagent_model is None:
            raise ValueError("subagent reasoning effort set without a model")
        if self.default_subagent_model not in SUPPORTED_MODEL_IDS:
            raise ValueError(f"unsupported subagent model: {self.default_subagent_model}")
        if self.subagent_reasoning_effort is None or not model_supports_effort(
            self.default_subagent_model,
            self.subagent_reasoning_effort,
        ):
            raise ValueError(
                f"effort {self.subagent_reasoning_effort!r} not supported by "
                f"{self.default_subagent_model!r}"
            )


def _normalize_stale_model_pair(model: str, effort: str | None) -> tuple[str, str | None]:
    if model in SUPPORTED_MODEL_IDS or effort is None:
        return model, effort
    fallback = provider_fallback_pair(model, effort)
    if fallback is None:
        return model, effort
    return fallback


def normalize_profile_for_response(profile: dict[str, Any]) -> dict[str, Any]:
    value = dict(profile)
    value.pop("create_prs", None)
    model = value.get("default_model")
    effort = value.get("reasoning_effort")
    if isinstance(model, str):
        value["default_model"], value["reasoning_effort"] = _normalize_stale_model_pair(
            model,
            effort if isinstance(effort, str) else None,
        )
    subagent_model = value.get("default_subagent_model")
    subagent_effort = value.get("subagent_reasoning_effort")
    if isinstance(subagent_model, str):
        value["default_subagent_model"], value["subagent_reasoning_effort"] = (
            _normalize_stale_model_pair(
                subagent_model,
                subagent_effort if isinstance(subagent_effort, str) else None,
            )
        )
    return value


async def get_profile(login: str) -> dict[str, Any] | None:
    return await get_value(PROFILES_NAMESPACE, login)


async def upsert_profile(login: str, email: str, update: ProfileUpdate) -> dict[str, Any]:
    """Write the user's editable settings."""
    existing = await get_profile(login) or {}
    value: dict[str, Any] = {
        **existing,
        "login": login,
        "email": email or existing.get("email", ""),
        "default_model": update.default_model,
        "reasoning_effort": update.reasoning_effort,
        "default_subagent_model": update.default_subagent_model,
        "subagent_reasoning_effort": update.subagent_reasoning_effort,
        "default_repo": update.default_repo,
        "base_branch": update.base_branch,
        "branch_prefix": update.branch_prefix,
        "auto_fix_ci": update.auto_fix_ci,
        "draft_prs": (
            update.draft_prs if update.draft_prs is not None else existing.get("draft_prs", True)
        ),
        "review_draft_prs": update.review_draft_prs,
        "updated_at": now_iso(),
    }
    for stale_field in (
        "first_name",
        "last_name",
        "allow_artifacts",
        "slack_notifications",
        "preferred_pr_destination",
        "create_prs",
    ):
        value.pop(stale_field, None)
    await put_value(PROFILES_NAMESPACE, login, value)
    return value


async def list_profiles() -> list[dict[str, Any]]:
    return await search_values(PROFILES_NAMESPACE, limit=1000)
