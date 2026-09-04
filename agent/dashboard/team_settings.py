"""Team-wide Open SWE Review (Bugbot) settings stored in LangGraph Store.

A single record keyed ``"default"`` keeps all instance-wide reviewer
configuration in one place. Per-repo style prompts live in
:mod:`agent.review.styles`.
"""

import logging
import os
import re
from typing import Any, Literal

from pydantic import BaseModel, field_validator, model_validator

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
from agent.store import get_value, now_iso, put_value
from agent.utils.gateway import resolve_gateway_enabled
from agent.utils.openai_oauth import desktop_openai_oauth_available

logger = logging.getLogger(__name__)

TEAM_SETTINGS_NAMESPACE: list[str] = ["team_settings"]
TEAM_SETTINGS_KEY = "default"

# Cap the org-wide guidelines so a runaway value can't dominate the reviewer
# prompt. Generous enough for a detailed policy, small enough to stay bounded.
ORG_GUIDELINES_MAX_CHARS = 10_000
REVIEW_TRACING_PROJECT_MAX_CHARS = 256
DEFAULT_THREAD_TITLE_MODEL = "openai:gpt-5.6-luna"
DEFAULT_THREAD_TITLE_REASONING_EFFORT = "low"
ANTHROPIC_THREAD_TITLE_MODEL = "anthropic:claude-haiku-4-5"
# Titles are a one-shot classification; no extended thinking needed.
ANTHROPIC_THREAD_TITLE_REASONING_EFFORT = "none"
DEFAULT_TRANSCRIPTION_MODEL = "gpt-transcribe"
TRANSCRIPTION_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class TranscriptionSettingsUpdate(BaseModel):
    transcription_model: str = DEFAULT_TRANSCRIPTION_MODEL

    @field_validator("transcription_model")
    @classmethod
    def _validate_transcription_model(cls, value: str) -> str:
        value = value.strip()
        if not TRANSCRIPTION_MODEL_RE.fullmatch(value):
            raise ValueError("invalid transcription model")
        return value


class TeamSettingsUpdate(TranscriptionSettingsUpdate):
    review_draft_prs: bool = False
    pr_summaries: bool = True
    review_trace_links: bool = True
    # Tri-state LLM Gateway toggle: True/False is authoritative, None inherits the
    # LANGSMITH_GATEWAY_ENABLED deployment default.
    gateway_enabled: bool | None = None
    transcription_model: str = DEFAULT_TRANSCRIPTION_MODEL
    fable_enabled: bool = False
    review_tracing_project: str | None = None
    org_guidelines: str | None = None
    default_agent_model: str | None = None
    default_agent_reasoning_effort: str | None = None
    default_agent_subagent_model: str | None = None
    default_agent_subagent_reasoning_effort: str | None = None
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

    @field_validator("review_tracing_project", mode="before")
    @classmethod
    def _normalize_review_tracing_project(cls, v: object) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("review_tracing_project must be a string")
        text = v.strip()
        if not text:
            return None
        if len(text) > REVIEW_TRACING_PROJECT_MAX_CHARS:
            raise ValueError(
                "review_tracing_project must be at most "
                f"{REVIEW_TRACING_PROJECT_MAX_CHARS} characters"
            )
        return text

    @model_validator(mode="after")
    def _validate_model_pairs(self) -> TeamSettingsUpdate:
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
        if self.fable_enabled:
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
        return self


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
    ("default_reviewer_model", "default_reviewer_reasoning_effort"),
    ("default_reviewer_subagent_model", "default_reviewer_subagent_reasoning_effort"),
    ("default_grouping_model", "default_grouping_reasoning_effort"),
    ("default_chat_model", "default_chat_reasoning_effort"),
    ("default_thread_title_model", "default_thread_title_reasoning_effort"),
)


def normalize_team_settings_for_response(settings: dict[str, Any]) -> dict[str, Any]:
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
    owner = os.environ.get("DEFAULT_REPO_OWNER", "").strip()
    name = os.environ.get("DEFAULT_REPO_NAME", "").strip()
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
        "gateway_enabled": None,
        "transcription_model": DEFAULT_TRANSCRIPTION_MODEL,
        "fable_enabled": False,
        "review_tracing_project": None,
        "org_guidelines": None,
        "default_agent_model": fallback_model,
        "default_agent_reasoning_effort": fallback_effort,
        "default_agent_subagent_model": fallback_model,
        "default_agent_subagent_reasoning_effort": fallback_effort,
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


async def get_team_settings() -> dict[str, Any]:
    """The team record merged over the hardcoded defaults.

    Fail-soft on purpose: the agent, the reviewer, and every webhook read this
    to pick a model, so an unreachable store must degrade to the defaults
    rather than fail every run at once.
    """
    defaults = _default_settings()
    try:
        value = await get_value(TEAM_SETTINGS_NAMESPACE, TEAM_SETTINGS_KEY)
    except Exception:
        logger.warning("team settings lookup failed; using defaults", exc_info=True)
        return defaults
    if value is None:
        return defaults
    # Skip None-valued model fields so legacy records (or PUTs that cleared the
    # selection) still surface the hardcoded default instead of a null.
    overlay = {k: v for k, v in value.items() if v is not None}
    merged = {**defaults, **overlay}
    for stale_field in (
        "trigger_mode",
        "autofix_mode",
        "autofix_severity_threshold",
        "autofix_enabled",
        "review_author_context_enabled",
    ):
        merged.pop(stale_field, None)
    return normalize_team_settings_for_response(merged)


async def upsert_team_settings(update: TeamSettingsUpdate) -> dict[str, Any]:
    value: dict[str, Any] = {
        "review_draft_prs": update.review_draft_prs,
        "pr_summaries": update.pr_summaries,
        "review_trace_links": update.review_trace_links,
        "gateway_enabled": update.gateway_enabled,
        "transcription_model": update.transcription_model,
        "fable_enabled": update.fable_enabled,
        "review_tracing_project": update.review_tracing_project,
        "org_guidelines": update.org_guidelines,
        "default_agent_model": update.default_agent_model,
        "default_agent_reasoning_effort": update.default_agent_reasoning_effort,
        "default_agent_subagent_model": update.default_agent_subagent_model,
        "default_agent_subagent_reasoning_effort": update.default_agent_subagent_reasoning_effort,
        "default_repo": update.default_repo,
        "default_reviewer_model": update.default_reviewer_model,
        "default_reviewer_reasoning_effort": update.default_reviewer_reasoning_effort,
        "default_reviewer_subagent_model": update.default_reviewer_subagent_model,
        "default_reviewer_subagent_reasoning_effort": update.default_reviewer_subagent_reasoning_effort,
        "default_grouping_model": update.default_grouping_model,
        "default_grouping_reasoning_effort": update.default_grouping_reasoning_effort,
        "default_chat_model": update.default_chat_model,
        "default_chat_reasoning_effort": update.default_chat_reasoning_effort,
        "default_thread_title_model": update.default_thread_title_model,
        "default_thread_title_reasoning_effort": update.default_thread_title_reasoning_effort,
        "updated_at": now_iso(),
    }
    await put_value(TEAM_SETTINGS_NAMESPACE, TEAM_SETTINGS_KEY, value)
    return value


async def get_team_default_repo() -> dict[str, str] | None:
    settings = await get_team_settings()
    return _parse_repo(settings.get("default_repo"))


async def get_team_default_model(
    role: Literal["agent", "reviewer", "chat"],
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
    settings = await get_team_settings()
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


async def get_team_default_model_pair(
    role: Literal["agent", "reviewer"],
) -> tuple[tuple[str, str], tuple[str, str]]:
    """Return default ``(main, subagent)`` model pairs for ``role`` from one store read."""
    settings = await get_team_settings()
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


async def get_team_default_grouping_model() -> tuple[str, str]:
    """Return the team-wide default ``(model_id, reasoning_effort)`` for the
    review diff-grouping pass.

    When no grouping-specific model is configured (or it's no longer
    supported), inherit the team **reviewer subagent** default — the grouping
    pass is a cheap, fast companion to the reviewer, so it should track that
    cheaper tier rather than the primary reviewer model.
    """
    settings = await get_team_settings()
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
    if (
        pair[0].startswith("openai:")
        and not gateway_enabled
        and not os.environ.get("OPENAI_API_KEY")
        and not desktop_openai_oauth_available()
        and os.environ.get("ANTHROPIC_API_KEY")
    ):
        return ANTHROPIC_THREAD_TITLE_MODEL, ANTHROPIC_THREAD_TITLE_REASONING_EFFORT
    return pair


async def get_team_default_thread_title_model() -> tuple[str, str]:
    settings = await get_team_settings()
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


async def get_team_review_trace_links_enabled() -> bool:
    """Return whether GitHub review bodies should include a LangSmith trace link."""
    settings = await get_team_settings()
    return bool(settings.get("review_trace_links", True))


async def get_team_transcription_model() -> str:
    value = (await get_team_settings()).get("transcription_model")
    return (
        value
        if isinstance(value, str) and TRANSCRIPTION_MODEL_RE.fullmatch(value)
        else DEFAULT_TRANSCRIPTION_MODEL
    )


async def update_team_transcription_model(model: str) -> dict[str, Any]:
    settings = await get_team_settings()
    settings["transcription_model"] = TranscriptionSettingsUpdate(
        transcription_model=model
    ).transcription_model
    settings.pop("updated_at", None)
    return await upsert_team_settings(TeamSettingsUpdate.model_validate(settings))


async def get_team_gateway_enabled() -> bool | None:
    """Return the stored LLM Gateway toggle (``None`` means inherit the env default)."""
    settings = await get_team_settings()
    value = settings.get("gateway_enabled")
    return value if isinstance(value, bool) else None


async def get_team_fable_enabled() -> bool:
    """Return whether Fable models are enabled for the team."""
    settings = await get_team_settings()
    value = settings.get("fable_enabled")
    return bool(value) if isinstance(value, bool) else False


async def get_effective_gateway_enabled() -> bool:
    """Resolve whether LLM Gateway routing is on: team setting, else env default."""
    return resolve_gateway_enabled(await get_team_gateway_enabled())


async def get_team_review_tracing_project() -> str | None:
    """Return the LangSmith tracing project used for PR trace resolution."""
    settings = await get_team_settings()
    value = settings.get("review_tracing_project")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


async def get_org_review_guidelines() -> str | None:
    """Return the org-wide reviewer guidelines supplement, if configured."""
    settings = await get_team_settings()
    value = settings.get("org_guidelines")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


async def get_team_default_subagent_model(
    role: Literal["agent", "reviewer"],
) -> tuple[str, str]:
    """Return the team-wide default subagent ``(model_id, reasoning_effort)`` for ``role``."""
    settings = await get_team_settings()
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
