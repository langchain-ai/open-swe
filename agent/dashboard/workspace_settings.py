"""Team-wide Open SWE Review (Bugbot) settings stored in LangGraph Store.

One record per workspace keeps that workspace's instance-wide reviewer
configuration in one place, keyed by workspace slug (the pre-workspaces
record lives at the ``"default"`` key, so the default workspace needs no
migration). Per-repo style prompts live in :mod:`agent.review.styles`.
"""

import logging
from collections.abc import Mapping
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
# The instance record keeps the key workspace settings had before workspaces existed,
# so an upgrade needs no data migration.
INSTANCE_SETTINGS_KEY = "default"
# One sparse record per workspace slug: a field that is missing or None
# inherits the instance value.
WORKSPACE_SETTINGS_NAMESPACE: list[str] = ["workspace_settings"]

# Cap the org-wide guidelines so a runaway value can't dominate the reviewer
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
    # Tri-state LLM Gateway toggle: True/False is authoritative, None inherits the
    # LANGSMITH_GATEWAY_ENABLED deployment default.
    # Tri-state adaptive model routing toggle: True/False is authoritative,
    # None is off (routing is opt-in until an admin enables it org-wide).
    model_routing_enabled: bool | None = None
    gateway_enabled: bool | None = None
    fable_enabled: bool | None = None
    org_guidelines: str | None = None
    default_agent_model: str | None = None
    default_agent_reasoning_effort: str | None = None
    default_agent_subagent_model: str | None = None
    default_agent_subagent_reasoning_effort: str | None = None
    default_agent_routing_fast_model: str | None = None
    default_agent_routing_fast_reasoning_effort: str | None = None
    default_agent_routing_fast_alt_model: str | None = None
    default_agent_routing_fast_alt_reasoning_effort: str | None = None
    # Probability that a fast-routed turn goes to the fast_alt model instead.
    default_agent_routing_fast_alt_probability: float | None = None
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
        for tier in ("fast", "fast_alt", "balanced", "performance"):
            model_field = f"default_agent_routing_{tier}_model"
            effort_field = f"default_agent_routing_{tier}_reasoning_effort"
            if not hasattr(self, model_field):
                continue
            model, effort = _normalize_stale_model_pair(
                getattr(self, model_field), getattr(self, effort_field)
            )
            setattr(self, model_field, model)
            setattr(self, effort_field, effort)
        if self.default_agent_routing_fast_alt_model is not None:
            if self.default_agent_routing_fast_model is None:
                raise ValueError("fast_alt model set without a fast model")
            probability = self.default_agent_routing_fast_alt_probability
            if probability is not None and not 0.0 <= probability <= 1.0:
                raise ValueError("fast_alt probability must be between 0.0 and 1.0")
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
        for tier in ("fast", "fast_alt", "balanced", "performance"):
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
        "default_agent_routing_fast_alt_model",
        "default_agent_routing_fast_alt_reasoning_effort",
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
        "org_guidelines": None,
        "default_agent_model": fallback_model,
        "default_agent_reasoning_effort": fallback_effort,
        "default_agent_subagent_model": fallback_model,
        "default_agent_subagent_reasoning_effort": fallback_effort,
        "default_agent_routing_fast_model": "fireworks:accounts/fireworks/models/glm-5p3-flash",
        "default_agent_routing_fast_reasoning_effort": "high",
        # A/B experiment: half of fast-routed turns go to Luna.
        "default_agent_routing_fast_alt_model": "openai:gpt-5.6-luna",
        "default_agent_routing_fast_alt_reasoning_effort": "high",
        "default_agent_routing_fast_alt_probability": 0.5,
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
    instance default: the HTTP layer rejects those before they reach here, and a
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
    PUTs that cleared a selection) never pin a null; an explicit 0 fast_alt
    probability stays, since zero is a valid "experiment off".
    """
    if not record:
        return {}
    return {
        k: v
        for k, v in record.items()
        if v is not None or k == "default_agent_routing_fast_alt_probability"
    }


def _finish(merged: dict[str, Any]) -> dict[str, Any]:
    for stale_field in _STALE_FIELDS:
        merged.pop(stale_field, None)
    return normalize_workspace_settings_for_response(merged)


async def _instance_record() -> dict[str, Any]:
    return _set_fields(await get_value(INSTANCE_SETTINGS_NAMESPACE, INSTANCE_SETTINGS_KEY))


async def _workspace_record(slug: str) -> dict[str, Any]:
    record = await get_value(WORKSPACE_SETTINGS_NAMESPACE, slug)
    if record is None and slug != DEFAULT_WORKSPACE_SLUG:
        # #2807 stored every workspace's record beside the instance one; a
        # record written there stays in force until the workspace is saved again.
        record = await get_value(INSTANCE_SETTINGS_NAMESPACE, slug)
    return _set_fields(record)


async def get_instance_settings() -> dict[str, Any]:
    """The instance record merged over the hardcoded defaults.

    Every workspace inherits these; see :func:`get_workspace_settings` for what a
    run actually sees.
    """
    defaults = _default_settings()
    try:
        instance = await _instance_record()
    except Exception:
        logger.warning("instance settings lookup failed; using defaults", exc_info=True)
        return defaults
    return _finish({**defaults, **instance})


async def get_workspace_settings(workspace: str | None = None) -> dict[str, Any]:
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
        return defaults
    return _finish({**defaults, **instance, **overrides})


class WorkspaceSettingsView(TypedDict):
    """What a workspace's settings editor needs: the effective values and which of them it set."""

    effective: dict[str, Any]
    overrides: dict[str, Any]


async def workspace_settings_view(slug: str) -> WorkspaceSettingsView:
    overrides = await _workspace_record(slug)
    overrides.pop("updated_at", None)
    return {"effective": await get_workspace_settings(slug), "overrides": overrides}


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
        fable_enabled = bool((await get_instance_settings())["fable_enabled"])
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


async def get_workspace_default_repo(workspace: str | None = None) -> dict[str, str] | None:
    settings = await get_workspace_settings(workspace)
    return _parse_repo(settings.get("default_repo"))


async def get_workspace_default_model(
    role: Literal["agent", "reviewer", "chat"],
    workspace: str | None = None,
) -> tuple[str, str]:
    """Return the team-wide default ``(model_id, reasoning_effort)`` for ``role``.

    Always returns a valid pair, resolved in order: the admin-configured pair if
    still supported; otherwise the newest supported model for the same provider
    (so a stale Anthropic/OpenAI selection stays on its provider rather than
    jumping cross-provider); otherwise the hardcoded global default from
    :func:`agent.dashboard.options.default_model_pair`.

    ``"chat"`` (the review-page PR chat) has no hardcoded default: when its
    admin setting is unset/invalid it inherits the team **agent** default.
    """
    settings = await get_workspace_settings(workspace)
    if role == "chat":
        model = settings.get("default_chat_model")
        effort = settings.get("default_chat_reasoning_effort")
        if (
            isinstance(model, str)
            and isinstance(effort, str)
            and model in SUPPORTED_MODEL_IDS
            and model_supports_effort(model, effort)
        ):
            return _resolve_default_pair(model, effort)
        # Inherit the Agent default when no chat-specific model is configured.
        model = settings.get("default_agent_model")
        effort = settings.get("default_agent_reasoning_effort")
    elif role == "agent":
        model = settings.get("default_agent_model")
        effort = settings.get("default_agent_reasoning_effort")
    else:
        model = settings.get("default_reviewer_model")
        effort = settings.get("default_reviewer_reasoning_effort")
    return _resolve_default_pair(model, effort)


async def get_workspace_default_model_pair(
    role: Literal["agent", "reviewer"],
    workspace: str | None = None,
) -> tuple[tuple[str, str], tuple[str, str]]:
    """Return default ``(main, subagent)`` model pairs for ``role`` from one store read."""
    settings = await get_workspace_settings(workspace)
    if role == "agent":
        main = _resolve_default_pair(
            settings.get("default_agent_model"),
            settings.get("default_agent_reasoning_effort"),
        )
        subagent = _resolve_default_pair(
            settings.get("default_agent_subagent_model"),
            settings.get("default_agent_subagent_reasoning_effort"),
        )
    else:
        main = _resolve_default_pair(
            settings.get("default_reviewer_model"),
            settings.get("default_reviewer_reasoning_effort"),
        )
        subagent = _resolve_default_pair(
            settings.get("default_reviewer_subagent_model"),
            settings.get("default_reviewer_subagent_reasoning_effort"),
        )
    return main, subagent


async def get_workspace_agent_routing_models(
    workspace: str | None = None,
) -> dict[str, tuple[str, str]]:
    settings = await get_workspace_settings(workspace)
    tiers = ("fast", "fast_alt", "balanced", "performance")
    models = {
        tier: _resolve_default_pair(
            settings.get(f"default_agent_routing_{tier}_model"),
            settings.get(f"default_agent_routing_{tier}_reasoning_effort"),
        )
        for tier in tiers
    }
    fast_alt_probability = settings.get("default_agent_routing_fast_alt_probability")
    # An explicit 0 probability is a valid "experiment off" configuration; only
    # drop the alt model when no probability (or an unparseable one) was stored
    # or the alt model was explicitly cleared.
    if (
        isinstance(fast_alt_probability, bool)
        or not isinstance(fast_alt_probability, int | float)
        or not 0.0 <= float(fast_alt_probability) <= 1.0
        or float(fast_alt_probability) == 0.0
    ):
        models.pop("fast_alt", None)
    return models


def get_workspace_fast_alt_probability(settings: Mapping[str, Any]) -> float:
    """The stored fast-route split, defaulting to the 50/50 experiment value.

    An explicit ``0`` disables the experiment (no fast turns go to the alt
    model); a missing or invalid value restores the default 50/50 split.
    """
    value = settings.get("default_agent_routing_fast_alt_probability")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.5
    probability = float(value)
    return probability if 0.0 <= probability <= 1.0 else 0.5


async def get_workspace_default_grouping_model(workspace: str | None = None) -> tuple[str, str]:
    """Return the team-wide default ``(model_id, reasoning_effort)`` for the
    review diff-grouping pass.

    When no grouping-specific model is configured (or it's no longer
    supported), inherit the team **reviewer subagent** default — the grouping
    pass is a cheap, fast companion to the reviewer, so it should track that
    cheaper tier rather than the primary reviewer model.
    """
    settings = await get_workspace_settings(workspace)
    model = settings.get("default_grouping_model")
    effort = settings.get("default_grouping_reasoning_effort")
    if (
        isinstance(model, str)
        and isinstance(effort, str)
        and model in SUPPORTED_MODEL_IDS
        and model not in NON_DEFAULT_MODEL_IDS
        and model_supports_effort(model, effort)
    ):
        return _resolve_default_pair(model, effort)
    return _resolve_default_pair(
        settings.get("default_reviewer_subagent_model"),
        settings.get("default_reviewer_subagent_reasoning_effort"),
    )


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


async def get_workspace_default_thread_title_model(workspace: str | None = None) -> tuple[str, str]:
    settings = await get_workspace_settings(workspace)
    model = settings.get("default_thread_title_model")
    effort = settings.get("default_thread_title_reasoning_effort")
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
    return _gate_openai_title_model(
        pair, gateway_enabled=resolve_gateway_enabled(settings.get("gateway_enabled"))
    )


async def get_workspace_review_trace_links_enabled(workspace: str | None = None) -> bool:
    """Return whether GitHub review bodies should include a LangSmith trace link."""
    settings = await get_workspace_settings(workspace)
    return bool(settings.get("review_trace_links", True))


async def get_workspace_model_routing_enabled(workspace: str | None = None) -> bool:
    """Return whether adaptive model routing is enabled org-wide."""
    settings = await get_workspace_settings(workspace)
    value = settings.get("model_routing_enabled")
    return value if isinstance(value, bool) else False


async def get_workspace_gateway_enabled(workspace: str | None = None) -> bool | None:
    """Return the stored LLM Gateway toggle (``None`` means inherit the env default)."""
    settings = await get_workspace_settings(workspace)
    value = settings.get("gateway_enabled")
    return value if isinstance(value, bool) else None


async def get_workspace_fable_enabled(workspace: str | None = None) -> bool:
    """Return whether Fable models are enabled for the team."""
    settings = await get_workspace_settings(workspace)
    value = settings.get("fable_enabled")
    return bool(value) if isinstance(value, bool) else False


async def get_effective_gateway_enabled(workspace: str | None = None) -> bool:
    """Resolve whether LLM Gateway routing is on: team setting, else env default."""
    return resolve_gateway_enabled(await get_workspace_gateway_enabled(workspace))


async def get_org_review_guidelines(workspace: str | None = None) -> str | None:
    """Return the org-wide reviewer guidelines supplement, if configured."""
    settings = await get_workspace_settings(workspace)
    value = settings.get("org_guidelines")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


async def get_workspace_default_subagent_model(
    role: Literal["agent", "reviewer"],
    workspace: str | None = None,
) -> tuple[str, str]:
    """Return the team-wide default subagent ``(model_id, reasoning_effort)`` for ``role``."""
    settings = await get_workspace_settings(workspace)
    if role == "agent":
        model = settings.get("default_agent_subagent_model")
        effort = settings.get("default_agent_subagent_reasoning_effort")
    else:
        model = settings.get("default_reviewer_subagent_model")
        effort = settings.get("default_reviewer_subagent_reasoning_effort")
    return _resolve_default_pair(model, effort)


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
    return await get_instance_settings()


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
