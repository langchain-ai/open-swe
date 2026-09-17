"""Workspace settings: model defaults, review toggles and guidelines, the gateway
and Fable toggles, and the default repository, stored in the LangGraph Store.

They resolve by tier. The instance record (the pre-workspaces "team settings"
record, still under its original key) applies to every workspace; a
workspace's own record holds only the fields an admin overrode there. Per-user
profile settings and a thread's ``configurable`` layer on top in the callers
that honour them. Per-repo style prompts live in :mod:`agent.review.styles`.
"""

import logging
from collections.abc import Iterator, Mapping
from typing import Any, Literal, TypedDict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator, model_validator

from agent.config import ENV
from agent.dashboard.deps import ADMIN_DEP, SESSION_DEP
from agent.dashboard.options import (
    DEPRECATED_MODEL_IDS,
    FABLE_MODEL_IDS,
    NON_DEFAULT_MODEL_IDS,
    SUPPORTED_MODEL_IDS,
    canonical_model_pair,
    default_model_pair,
    gate_fable_model,
    model_supports_effort,
    provider_fallback_pair,
)
from agent.run_config import RunConfig
from agent.store import delete_value, get_value, now_iso, put_value
from agent.utils.gateway import gateway_overrides, resolve_gateway_enabled
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES, slugify

logger = logging.getLogger(__name__)

INSTANCE_SETTINGS_NAMESPACE: list[str] = ["team_settings"]
# The instance record keeps the key the pre-workspaces ("team settings") record
# used, so an upgrade needs no data migration.
INSTANCE_SETTINGS_KEY = "default"
# One sparse record per workspace slug: a field that is missing or None
# inherits the instance value.
WORKSPACE_SETTINGS_NAMESPACE: list[str] = ["workspace_settings"]

# Cap the guidelines so a runaway value can't dominate the reviewer
# prompt. Generous enough for a detailed policy, small enough to stay bounded.
ORG_GUIDELINES_MAX_CHARS = 10_000
DEFAULT_THREAD_TITLE_MODEL = "openai:gpt-5.6-luna"
DEFAULT_THREAD_TITLE_REASONING_EFFORT = "low"
ANTHROPIC_THREAD_TITLE_MODEL = "anthropic:claude-haiku-4-5"
# Titles are a one-shot classification; no extended thinking needed.
ANTHROPIC_THREAD_TITLE_REASONING_EFFORT = "none"


class WorkspaceSettingsUpdate(BaseModel):
    """A settings record at either tier.

    Every field is optional. On the instance record, None means the hardcoded
    default; on a workspace's record, None inherits the instance value.
    """

    review_draft_prs: bool | None = None
    pr_summaries: bool | None = None
    review_trace_links: bool | None = None
    # Tri-state LLM Gateway toggle: True/False is authoritative. None on the
    # instance inherits the LANGSMITH_GATEWAY_ENABLED deployment default; None on
    # a workspace inherits the instance.
    # Tri-state adaptive model routing toggle: True/False is authoritative. None
    # on the instance is off (routing is opt-in); None on a workspace inherits.
    model_routing_enabled: bool | None = None
    gateway_enabled: bool | None = None
    fable_enabled: bool | None = None
    expedited_review_enabled: bool | None = None
    org_guidelines: str | None = None
    default_agent_model: str | None = None
    default_agent_reasoning_effort: str | None = None
    default_agent_subagent_model: str | None = None
    default_agent_subagent_reasoning_effort: str | None = None
    default_agent_routing_fast_model: str | None = None
    default_agent_routing_fast_reasoning_effort: str | None = None
    default_agent_routing_balanced_model: str | None = None
    default_agent_routing_balanced_reasoning_effort: str | None = None
    default_agent_routing_performance_model: str | None = None
    default_agent_routing_performance_reasoning_effort: str | None = None
    default_repo: str | None = None
    default_reviewer_model: str | None = None
    default_reviewer_reasoning_effort: str | None = None
    default_reviewer_subagent_model: str | None = None
    default_reviewer_subagent_reasoning_effort: str | None = None
    default_grouping_model: str | None = None
    default_grouping_reasoning_effort: str | None = None
    default_chat_model: str | None = None
    default_chat_reasoning_effort: str | None = None
    default_thread_title_model: str | None = None
    default_thread_title_reasoning_effort: str | None = None

    @field_validator("org_guidelines", mode="before")
    @classmethod
    def _normalize_org_guidelines(cls, v: object) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("org_guidelines must be a string")
        text = v.strip()
        if not text:
            return None
        if len(text) > ORG_GUIDELINES_MAX_CHARS:
            raise ValueError(
                f"org_guidelines must be at most {ORG_GUIDELINES_MAX_CHARS} characters"
            )
        return text

    @model_validator(mode="after")
    def _validate_model_pairs(self) -> WorkspaceSettingsUpdate:
        self.default_agent_model, self.default_agent_reasoning_effort = _normalize_stale_model_pair(
            self.default_agent_model,
            self.default_agent_reasoning_effort,
        )
        self.default_agent_subagent_model, self.default_agent_subagent_reasoning_effort = (
            _normalize_stale_model_pair(
                self.default_agent_subagent_model,
                self.default_agent_subagent_reasoning_effort,
            )
        )
        for tier in ("fast", "balanced", "performance"):
            model_field = f"default_agent_routing_{tier}_model"
            effort_field = f"default_agent_routing_{tier}_reasoning_effort"
            if not hasattr(self, model_field):
                continue
            model, effort = _normalize_stale_model_pair(
                getattr(self, model_field), getattr(self, effort_field)
            )
            setattr(self, model_field, model)
            setattr(self, effort_field, effort)
        self.default_reviewer_model, self.default_reviewer_reasoning_effort = (
            _normalize_stale_model_pair(
                self.default_reviewer_model,
                self.default_reviewer_reasoning_effort,
            )
        )
        (
            self.default_reviewer_subagent_model,
            self.default_reviewer_subagent_reasoning_effort,
        ) = _normalize_stale_model_pair(
            self.default_reviewer_subagent_model,
            self.default_reviewer_subagent_reasoning_effort,
        )
        self.default_grouping_model, self.default_grouping_reasoning_effort = (
            _normalize_stale_model_pair(
                self.default_grouping_model,
                self.default_grouping_reasoning_effort,
            )
        )
        self.default_chat_model, self.default_chat_reasoning_effort = _normalize_stale_model_pair(
            self.default_chat_model,
            self.default_chat_reasoning_effort,
        )
        self.default_thread_title_model, self.default_thread_title_reasoning_effort = (
            _normalize_stale_model_pair(
                self.default_thread_title_model,
                self.default_thread_title_reasoning_effort,
            )
        )
        _validate_model_effort_pair(
            self.default_agent_model, self.default_agent_reasoning_effort, "agent"
        )
        _validate_model_effort_pair(
            self.default_agent_subagent_model,
            self.default_agent_subagent_reasoning_effort,
            "agent subagent",
        )
        for tier in ("fast", "balanced", "performance"):
            _validate_model_effort_pair(
                getattr(self, f"default_agent_routing_{tier}_model"),
                getattr(self, f"default_agent_routing_{tier}_reasoning_effort"),
                f"agent routing {tier}",
            )
        _validate_model_effort_pair(
            self.default_reviewer_model, self.default_reviewer_reasoning_effort, "reviewer"
        )
        _validate_model_effort_pair(
            self.default_reviewer_subagent_model,
            self.default_reviewer_subagent_reasoning_effort,
            "reviewer subagent",
        )
        _validate_model_effort_pair(
            self.default_grouping_model,
            self.default_grouping_reasoning_effort,
            "review diff grouping",
        )
        _validate_model_effort_pair(
            self.default_chat_model, self.default_chat_reasoning_effort, "review chat"
        )
        _validate_model_effort_pair(
            self.default_thread_title_model,
            self.default_thread_title_reasoning_effort,
            "thread title",
        )
        return self

    def apply_fable_policy(self, *, fable_enabled: bool) -> None:
        """Enforce the Fable rules against the toggle this record resolves to.

        Applied at write time rather than in validation: a workspace record may
        inherit the toggle from the instance, so the payload alone cannot say
        whether Fable is on.
        """
        if fable_enabled:
            for model_field, _ in _MODEL_PAIR_FIELDS:
                model = getattr(self, model_field)
                if model in NON_DEFAULT_MODEL_IDS:
                    raise ValueError(f"{model!r} cannot be a default model")
        else:
            # Disabling Fable is the ZDR kill switch and must always succeed: rather
            # than reject a payload that still carries a Fable default, swap each
            # Fable default to its safe non-Fable fallback (mirrors the runtime
            # gate_fable_model guard) so the stored record can't advertise Fable.
            for model_field, effort_field in (
                ("default_agent_model", "default_agent_reasoning_effort"),
                ("default_agent_subagent_model", "default_agent_subagent_reasoning_effort"),
                (
                    "default_agent_routing_fast_model",
                    "default_agent_routing_fast_reasoning_effort",
                ),
                (
                    "default_agent_routing_balanced_model",
                    "default_agent_routing_balanced_reasoning_effort",
                ),
                (
                    "default_agent_routing_performance_model",
                    "default_agent_routing_performance_reasoning_effort",
                ),
                ("default_reviewer_model", "default_reviewer_reasoning_effort"),
                ("default_reviewer_subagent_model", "default_reviewer_subagent_reasoning_effort"),
                ("default_grouping_model", "default_grouping_reasoning_effort"),
                ("default_chat_model", "default_chat_reasoning_effort"),
                ("default_thread_title_model", "default_thread_title_reasoning_effort"),
            ):
                model = getattr(self, model_field)
                if model in FABLE_MODEL_IDS:
                    new_model, new_effort = gate_fable_model(
                        model, getattr(self, effort_field), fable_enabled=False
                    )
                    setattr(self, model_field, new_model)
                    setattr(self, effort_field, new_effort)


def _validate_model_effort_pair(model: str | None, effort: str | None, role: str) -> None:
    if model is None and effort is None:
        return
    if model is None:
        raise ValueError(f"{role} reasoning effort set without a model")
    if model not in SUPPORTED_MODEL_IDS:
        raise ValueError(f"unsupported {role} model: {model}")
    if effort is None or not model_supports_effort(model, effort):
        raise ValueError(f"effort {effort!r} not supported by {role} model {model!r}")


def _normalize_stale_model_pair(
    model: str | None, effort: str | None
) -> tuple[str | None, str | None]:
    if model in DEPRECATED_MODEL_IDS:
        return None, None
    canonical = canonical_model_pair(model, effort)
    if canonical is not None:
        return canonical
    return model, effort


_MODEL_PAIR_FIELDS: tuple[tuple[str, str], ...] = (
    ("default_agent_model", "default_agent_reasoning_effort"),
    ("default_agent_subagent_model", "default_agent_subagent_reasoning_effort"),
    ("default_agent_routing_fast_model", "default_agent_routing_fast_reasoning_effort"),
    (
        "default_agent_routing_balanced_model",
        "default_agent_routing_balanced_reasoning_effort",
    ),
    (
        "default_agent_routing_performance_model",
        "default_agent_routing_performance_reasoning_effort",
    ),
    ("default_reviewer_model", "default_reviewer_reasoning_effort"),
    ("default_reviewer_subagent_model", "default_reviewer_subagent_reasoning_effort"),
    ("default_grouping_model", "default_grouping_reasoning_effort"),
    ("default_chat_model", "default_chat_reasoning_effort"),
    ("default_thread_title_model", "default_thread_title_reasoning_effort"),
)


def normalize_workspace_settings_for_response(settings: dict[str, Any]) -> dict[str, Any]:
    value = dict(settings)
    for model_field, effort_field in _MODEL_PAIR_FIELDS:
        model = value.get(model_field)
        effort = value.get(effort_field)
        if isinstance(model, str):
            value[model_field], value[effort_field] = _normalize_stale_model_pair(
                model,
                effort if isinstance(effort, str) else None,
            )
    return value


def _env_default_repo() -> str | None:
    owner = ENV.DEFAULT_REPO_OWNER.get("").strip()
    name = ENV.DEFAULT_REPO_NAME.get().strip()
    return f"{owner}/{name}" if owner and name else None


def _parse_repo(value: object) -> dict[str, str] | None:
    if not isinstance(value, str):
        return None
    owner, sep, name = value.strip().partition("/")
    if not sep or not owner.strip() or not name.strip():
        return None
    return {"owner": owner.strip(), "name": name.strip()}


def _default_settings() -> dict[str, Any]:
    fallback_model, fallback_effort = default_model_pair()
    return {
        "review_draft_prs": False,
        "pr_summaries": True,
        "review_trace_links": True,
        "model_routing_enabled": None,
        "gateway_enabled": None,
        "fable_enabled": False,
        "expedited_review_enabled": False,
        "org_guidelines": None,
        "default_agent_model": fallback_model,
        "default_agent_reasoning_effort": fallback_effort,
        "default_agent_subagent_model": fallback_model,
        "default_agent_subagent_reasoning_effort": fallback_effort,
        "default_agent_routing_fast_model": "openai:gpt-5.6-luna",
        "default_agent_routing_fast_reasoning_effort": "high",
        "default_agent_routing_balanced_model": "openai:gpt-5.6-sol",
        "default_agent_routing_balanced_reasoning_effort": "medium",
        "default_agent_routing_performance_model": "openai:gpt-6-astra",
        "default_agent_routing_performance_reasoning_effort": "low",
        "default_repo": _env_default_repo(),
        "default_reviewer_model": fallback_model,
        "default_reviewer_reasoning_effort": fallback_effort,
        "default_reviewer_subagent_model": fallback_model,
        "default_reviewer_subagent_reasoning_effort": fallback_effort,
        # No hardcoded grouping default: unset means "inherit the Reviewer
        # subagent default".
        "default_grouping_model": None,
        "default_grouping_reasoning_effort": None,
        # No hardcoded chat default: unset means "inherit the Agent default".
        "default_chat_model": None,
        "default_chat_reasoning_effort": None,
        "default_thread_title_model": DEFAULT_THREAD_TITLE_MODEL,
        "default_thread_title_reasoning_effort": DEFAULT_THREAD_TITLE_REASONING_EFFORT,
        "updated_at": None,
    }


def resolve_settings_workspace(explicit: str | None = None) -> str:
    """Which workspace's settings apply: the caller's, else the running run's, else default.

    The name is slugified, so one spelling of a workspace cannot address a
    record another spelling misses. A name with nothing to slugify reads as the
    default workspace: the HTTP layer rejects those before they reach here, and a
    run must not die over a settings lookup.
    """
    candidate = explicit
    if not (isinstance(candidate, str) and candidate.strip()):
        try:
            candidate = RunConfig.from_runtime().workspace_slug
        except Exception:  # noqa: BLE001
            candidate = None
    if not (isinstance(candidate, str) and candidate.strip()):
        return DEFAULT_WORKSPACE_SLUG
    try:
        return slugify(candidate)
    except ValueError:
        logger.warning("unslugifiable workspace name; using the instance default")
        return DEFAULT_WORKSPACE_SLUG


_STALE_FIELDS = (
    "trigger_mode",
    "autofix_mode",
    "autofix_severity_threshold",
    "autofix_enabled",
    "review_author_context_enabled",
    "review_tracing_project",
    "transcription_model",
)


def _set_fields(record: Mapping[str, Any] | None) -> dict[str, Any]:
    """The fields of a stored record that carry a value.

    None-valued fields fall through to the tier below, so legacy records (or
    PUTs that cleared a selection) never pin a null.
    """
    if not record:
        return {}
    return {k: v for k, v in record.items() if v is not None}


def _finish(merged: dict[str, Any]) -> WorkspaceSettings:
    for stale_field in _STALE_FIELDS:
        merged.pop(stale_field, None)
    return WorkspaceSettings(normalize_workspace_settings_for_response(merged))


async def _instance_record() -> dict[str, Any]:
    return _set_fields(await get_value(INSTANCE_SETTINGS_NAMESPACE, INSTANCE_SETTINGS_KEY))


async def _workspace_record(slug: str) -> dict[str, Any]:
    record = await get_value(WORKSPACE_SETTINGS_NAMESPACE, slug)
    if record is None and slug != DEFAULT_WORKSPACE_SLUG:
        # #2807 stored every workspace's record beside the instance one; a
        # record written there stays in force until the workspace is saved again.
        record = await get_value(INSTANCE_SETTINGS_NAMESPACE, slug)
    return _set_fields(record)


async def get_instance_settings() -> WorkspaceSettings:
    """The instance record merged over the hardcoded defaults.

    Every workspace inherits these; see :func:`get_workspace_settings` for what a
    run actually sees.
    """
    defaults = _default_settings()
    try:
        instance = await _instance_record()
    except Exception:
        logger.warning("instance settings lookup failed; using defaults", exc_info=True)
        return WorkspaceSettings(defaults)
    return _finish({**defaults, **instance})


async def get_workspace_settings(workspace: str | None = None) -> WorkspaceSettings:
    """The settings a run in ``workspace`` sees.

    Tiered: the hardcoded defaults, then the instance record, then the
    workspace's own overrides. Per-user profile settings and the thread's
    ``configurable`` layer on top of this in the callers that honour them.

    Fail-soft on purpose: the agent, the reviewer, and every webhook read this
    to pick a model, so an unreachable store must degrade to the defaults
    rather than fail every run at once.
    """
    defaults = _default_settings()
    slug = resolve_settings_workspace(workspace)
    try:
        instance = await _instance_record()
        overrides = await _workspace_record(slug)
    except Exception:
        logger.warning("workspace settings lookup failed; using defaults", exc_info=True)
        return WorkspaceSettings(defaults)
    return _finish({**defaults, **instance, **overrides})


class WorkspaceSettingsView(TypedDict):
    """What a workspace's settings editor needs: the effective values and which of them it set."""

    effective: dict[str, Any]
    overrides: dict[str, Any]


async def workspace_settings_view(slug: str) -> WorkspaceSettingsView:
    overrides = await _workspace_record(slug)
    overrides.pop("updated_at", None)
    return {"effective": dict(await get_workspace_settings(slug)), "overrides": overrides}


def _record_values(update: WorkspaceSettingsUpdate) -> dict[str, Any]:
    return {**update.model_dump(), "updated_at": now_iso()}


async def upsert_instance_settings(update: WorkspaceSettingsUpdate) -> dict[str, Any]:
    """Replace the instance record. Raises ``ValueError`` for a Fable model saved as a default."""
    update.apply_fable_policy(fable_enabled=bool(update.fable_enabled))
    value = _record_values(update)
    await put_value(INSTANCE_SETTINGS_NAMESPACE, INSTANCE_SETTINGS_KEY, value)
    return value


async def upsert_workspace_overrides(
    slug: str, update: WorkspaceSettingsUpdate
) -> WorkspaceSettingsView:
    """Replace the workspace's overrides; a field left None inherits the instance value.

    Raises ``ValueError`` for a Fable model saved as a default while Fable is on,
    whether the workspace sets that toggle itself or inherits it.
    """
    fable_enabled = update.fable_enabled
    if fable_enabled is None:
        fable_enabled = (await get_instance_settings()).fable_enabled
    update.apply_fable_policy(fable_enabled=fable_enabled)
    value = {k: v for k, v in _record_values(update).items() if v is not None}
    await put_value(WORKSPACE_SETTINGS_NAMESPACE, slug, value)
    if slug != DEFAULT_WORKSPACE_SLUG:
        await delete_value(INSTANCE_SETTINGS_NAMESPACE, slug)
    return await workspace_settings_view(slug)


async def delete_workspace_settings(slug: str) -> None:
    """Forget the workspace's overrides, wherever they were written."""
    await delete_value(WORKSPACE_SETTINGS_NAMESPACE, slug)
    if slug != DEFAULT_WORKSPACE_SLUG:
        await delete_value(INSTANCE_SETTINGS_NAMESPACE, slug)


def _gate_openai_title_model(pair: tuple[str, str], *, gateway_enabled: bool) -> tuple[str, str]:
    """Swap an OpenAI title model for Haiku on Anthropic-only deployments.

    Title generation is the one model choice users rarely revisit, so an
    Anthropic-only install would otherwise fail every title with a missing
    OPENAI_API_KEY.
    """
    if not pair[0].startswith("openai:"):
        return pair
    # The toggle alone isn't enough: without a LangSmith key the gateway is
    # bypassed and the call still needs a real OpenAI credential.
    if gateway_enabled and gateway_overrides(pair[0]) is not None:
        return pair
    from agent.utils.openai_oauth import desktop_openai_oauth_available

    if ENV.OPENAI_API_KEY.optional() or desktop_openai_oauth_available():
        return pair
    if not ENV.ANTHROPIC_API_KEY.optional():
        return pair
    return ANTHROPIC_THREAD_TITLE_MODEL, ANTHROPIC_THREAD_TITLE_REASONING_EFFORT


def _resolve_default_pair(model: object, effort: object) -> tuple[str, str]:
    """Supported pair if valid, else same-provider fallback, else global default."""
    if (
        isinstance(model, str)
        and isinstance(effort, str)
        and model in SUPPORTED_MODEL_IDS
        and model not in NON_DEFAULT_MODEL_IDS
        and model_supports_effort(model, effort)
    ):
        return model, effort
    provider_pair = provider_fallback_pair(model, effort)
    if provider_pair is not None:
        return provider_pair
    return default_model_pair()


class WorkspaceSettings(Mapping[str, Any]):
    """The settings a run in one workspace sees, and the choices derived from them.

    A read-only mapping of the effective fields (hardcoded defaults, then the
    instance record, then the workspace's overrides) plus the one place the
    derivation rules live: which model a role runs, whether a toggle is on, and
    so on. Built by :func:`get_workspace_settings`.
    """

    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values = dict(values)

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"WorkspaceSettings({self._values!r})"

    @property
    def default_repo(self) -> dict[str, str] | None:
        return _parse_repo(self.get("default_repo"))

    def default_model(self, role: Literal["agent", "reviewer", "chat"]) -> tuple[str, str]:
        """The default ``(model_id, reasoning_effort)`` for ``role``.

        Always a valid pair, resolved in order: the configured pair if still
        supported; otherwise the newest supported model for the same provider
        (so a stale Anthropic/OpenAI selection stays on its provider rather
        than jumping cross-provider); otherwise the hardcoded global default
        from :func:`agent.dashboard.options.default_model_pair`.

        ``"chat"`` (the review-page PR chat) has no hardcoded default: when its
        setting is unset/invalid it inherits the **agent** default.
        """
        if role == "chat":
            model = self.get("default_chat_model")
            effort = self.get("default_chat_reasoning_effort")
            if (
                isinstance(model, str)
                and isinstance(effort, str)
                and model in SUPPORTED_MODEL_IDS
                and model_supports_effort(model, effort)
            ):
                return _resolve_default_pair(model, effort)
            # Inherit the Agent default when no chat-specific model is configured.
            model = self.get("default_agent_model")
            effort = self.get("default_agent_reasoning_effort")
        elif role == "agent":
            model = self.get("default_agent_model")
            effort = self.get("default_agent_reasoning_effort")
        else:
            model = self.get("default_reviewer_model")
            effort = self.get("default_reviewer_reasoning_effort")
        return _resolve_default_pair(model, effort)

    def default_subagent_model(self, role: Literal["agent", "reviewer"]) -> tuple[str, str]:
        """The default subagent ``(model_id, reasoning_effort)`` for ``role``."""
        if role == "agent":
            model = self.get("default_agent_subagent_model")
            effort = self.get("default_agent_subagent_reasoning_effort")
        else:
            model = self.get("default_reviewer_subagent_model")
            effort = self.get("default_reviewer_subagent_reasoning_effort")
        return _resolve_default_pair(model, effort)

    def default_model_pair(
        self, role: Literal["agent", "reviewer"]
    ) -> tuple[tuple[str, str], tuple[str, str]]:
        """The default ``(main, subagent)`` model pairs for ``role``."""
        return self.default_model(role), self.default_subagent_model(role)

    @property
    def agent_routing_models(self) -> dict[str, tuple[str, str]]:
        return {
            tier: _resolve_default_pair(
                self.get(f"default_agent_routing_{tier}_model"),
                self.get(f"default_agent_routing_{tier}_reasoning_effort"),
            )
            for tier in ("fast", "balanced", "performance")
        }

    @property
    def default_grouping_model(self) -> tuple[str, str]:
        """The default ``(model_id, reasoning_effort)`` for the review diff-grouping pass.

        When no grouping-specific model is configured (or it's no longer
        supported), inherit the **reviewer subagent** default: the grouping
        pass is a cheap, fast companion to the reviewer, so it should track that
        cheaper tier rather than the primary reviewer model.
        """
        model = self.get("default_grouping_model")
        effort = self.get("default_grouping_reasoning_effort")
        if (
            isinstance(model, str)
            and isinstance(effort, str)
            and model in SUPPORTED_MODEL_IDS
            and model not in NON_DEFAULT_MODEL_IDS
            and model_supports_effort(model, effort)
        ):
            return _resolve_default_pair(model, effort)
        return self.default_subagent_model("reviewer")

    @property
    def default_thread_title_model(self) -> tuple[str, str]:
        model = self.get("default_thread_title_model")
        effort = self.get("default_thread_title_reasoning_effort")
        if (
            isinstance(model, str)
            and isinstance(effort, str)
            and model in SUPPORTED_MODEL_IDS
            and model not in NON_DEFAULT_MODEL_IDS
            and model_supports_effort(model, effort)
        ):
            pair = _resolve_default_pair(model, effort)
        else:
            pair = DEFAULT_THREAD_TITLE_MODEL, DEFAULT_THREAD_TITLE_REASONING_EFFORT
        return _gate_openai_title_model(pair, gateway_enabled=self.effective_gateway_enabled)

    @property
    def review_trace_links_enabled(self) -> bool:
        """Whether GitHub review bodies should include a LangSmith trace link."""
        return bool(self.get("review_trace_links", True))

    @property
    def model_routing_enabled(self) -> bool:
        value = self.get("model_routing_enabled")
        return value if isinstance(value, bool) else False

    @property
    def gateway_enabled(self) -> bool | None:
        """The stored LLM Gateway toggle (``None`` means inherit the env default)."""
        value = self.get("gateway_enabled")
        return value if isinstance(value, bool) else None

    @property
    def effective_gateway_enabled(self) -> bool:
        """Whether LLM Gateway routing is on: the stored toggle, else the env default."""
        return resolve_gateway_enabled(self.gateway_enabled)

    @property
    def fable_enabled(self) -> bool:
        value = self.get("fable_enabled")
        return bool(value) if isinstance(value, bool) else False

    @property
    def expedited_review_enabled(self) -> bool:
        """Whether the experimental expedited Slack review is switched on."""
        value = self.get("expedited_review_enabled")
        return value if isinstance(value, bool) else False

    @property
    def org_review_guidelines(self) -> str | None:
        """The reviewer guidelines supplement, if any."""
        value = self.get("org_guidelines")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None


router = APIRouter(tags=["settings"])


def _normalized_workspace(raw: str) -> str:
    try:
        return slugify(raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


async def _existing_workspace(raw: str) -> str:
    slug = _normalized_workspace(raw)
    if await WORKSPACES.get(slug) is None:
        raise HTTPException(404, "workspace not found")
    return slug


@router.get("/settings")
# The pre-workspaces path, kept for the dashboard until it moves; hidden from the schema.
@router.get("/team-settings", include_in_schema=False)
async def api_get_instance_settings(_session: dict[str, Any] = SESSION_DEP) -> dict[str, Any]:
    """The instance record: what every workspace inherits."""
    return dict(await get_instance_settings())


@router.put("/settings")
@router.put("/team-settings", include_in_schema=False)
async def api_put_instance_settings(
    body: WorkspaceSettingsUpdate, _admin: dict[str, Any] = ADMIN_DEP
) -> dict[str, Any]:
    try:
        return await upsert_instance_settings(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/workspaces/{workspace}/settings")
async def api_get_workspace_settings(
    workspace: str, _session: dict[str, Any] = SESSION_DEP
) -> WorkspaceSettingsView:
    return await workspace_settings_view(await _existing_workspace(workspace))


@router.put("/workspaces/{workspace}/settings")
async def api_put_workspace_settings(
    workspace: str,
    body: WorkspaceSettingsUpdate,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> WorkspaceSettingsView:
    try:
        return await upsert_workspace_overrides(await _existing_workspace(workspace), body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
