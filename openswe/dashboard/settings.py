"""Global Robin defaults and their derived model and review choices."""

import asyncio
import logging
import re
from collections.abc import Iterator, Mapping
from typing import Any, Literal, NamedTuple, NotRequired, TypedDict
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from openswe.audit_logs.context import current_audit_log
from openswe.audit_logs.middleware import audit_endpoint
from openswe.audit_logs.models import SettingsChange
from openswe.config import ENV
from openswe.dashboard.deps import ADMIN_DEP, SESSION_DEP
from openswe.dashboard.options import (
    FABLE_MODEL_IDS,
    NON_DEFAULT_MODEL_IDS,
    SUPPORTED_MODEL_IDS,
    default_model_pair,
    gate_fable_model,
    model_supports_effort,
    normalize_model_choice,
)
from openswe.dashboard.settings_records import INSTANCE_SETTINGS
from openswe.database import postgres
from openswe.github.repo_names import normalize_repo_full_name
from openswe.ui_invalidations import Topic
from openswe.utils.time import now_iso

logger = logging.getLogger(__name__)

# Cap the guidelines so a runaway value can't dominate the reviewer
# prompt. Generous enough for a detailed policy, small enough to stay bounded.
ORG_GUIDELINES_MAX_CHARS = 10_000
DEFAULT_THREAD_TITLE_MODEL = "openai:gpt-6-luna"
DEFAULT_THREAD_TITLE_REASONING_EFFORT = "low"
REVIEW_SCOUT_FALLBACK_MODEL = ("openai:gpt-6.1-sol", "medium")
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN = re.compile(rf"(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})+")
# An MCP connection's name, then the name of a tool on its server.
_SESSION_TOOL = re.compile(r"[A-Za-z0-9_.-]{1,128}")


class AppRelease(TypedDict):
    """An app pinned to the release an admin approved, so its owner's later releases change
    nothing until an admin picks them up."""

    app: str
    release: str


class FeaturedApp(AppRelease):
    """An app in everyone's web UI, and the repositories it reads for each person who can."""

    repositories: NotRequired[list[str]]


class SettingsUpdate(BaseModel):
    """Global settings; omitted fields use their built-in defaults."""

    review_draft_prs: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    implementation_walkthrough_enabled: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    concise_walkthrough_enabled: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    pr_summaries: bool | None = Field(default=None, json_schema_extra={"agent_feature_flag": True})
    review_trace_links: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    review_apps: list[AppRelease] | None = Field(default=None, max_length=20)
    """The apps whose review step reviews follow, each at the release an admin approved;
    managed with ``manage_review_apps``."""
    review_app_preview_checks: list[str] | None = Field(default=None, max_length=10)
    """Checks, by name, such as a deploy workflow's last job, whose success for a pull
    request's head means its preview serves that commit: a review that works for apps then
    does that work on it; managed with ``manage_review_apps``."""
    # Where the browser of a review that works for apps may sign in, each domain with its
    # subdomains, and the tool of Robin's bot kit that signs it in, by its name in the kit.
    # Only the instance record's values are read: they decide where reviews sign in, and
    # as whom.
    review_browser_domains: list[str] | None = Field(default=None, max_length=20)
    review_browser_session_tool: str | None = None
    featured_apps: list[FeaturedApp] | None = Field(default=None, max_length=20)
    """Listed apps added to everyone's web UI, their permissions approved for them, each at
    the release an instance admin approved and sharing its ``repositories`` with each person
    who can read them. Only the instance record's value is read."""
    model_routing_enabled: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    # Routing v2: with routing on, threads split 50/50 between the Balanced and
    # Performance models by thread instead of going through the classifier.
    model_routing_split: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    model_selection_enabled: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    fable_enabled: bool | None = Field(default=None, json_schema_extra={"agent_feature_flag": True})
    expedited_review_enabled: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    slack_follow_up_suggestions: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    human_review_auto_merge_enabled: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    human_review_auto_assign_minutes: int | None = Field(default=None, ge=1, strict=True)
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
    default_review_scout_model: str | None = None
    default_review_scout_reasoning_effort: str | None = None
    default_thread_title_model: str | None = None
    default_thread_title_reasoning_effort: str | None = None
    # The share of newly reviewed pull requests that also get a shadow review on this model.
    review_shadow_percent: int | None = Field(default=None, ge=0, le=100, strict=True)
    review_shadow_model: str | None = None
    review_shadow_reasoning_effort: str | None = None

    @field_validator("review_apps")
    @classmethod
    def _app_releases(cls, v: list[AppRelease] | None) -> list[AppRelease] | None:
        if v is None:
            return None
        chosen: dict[str, str] = {}
        try:
            for entry in v:
                chosen[str(UUID(entry["app"].strip()))] = str(UUID(entry["release"].strip()))
        except ValueError:
            raise ValueError("an app is an app id and the id of its release") from None
        return [AppRelease(app=app, release=release) for app, release in chosen.items()]

    @field_validator("featured_apps")
    @classmethod
    def _featured_apps(cls, v: list[FeaturedApp] | None) -> list[FeaturedApp] | None:
        if v is None:
            return None
        chosen: dict[str, FeaturedApp] = {}
        try:
            for entry in v:
                repositories = list(
                    dict.fromkeys(
                        normalize_repo_full_name(name).lower()
                        for name in entry.get("repositories", [])
                    )
                )
                if len(repositories) > 10:
                    raise ValueError("an app shares at most 10 repositories for everyone")
                app = str(UUID(entry["app"].strip()))
                chosen[app] = FeaturedApp(
                    app=app,
                    release=str(UUID(entry["release"].strip())),
                    repositories=repositories,
                )
        except ValueError as exc:
            raise ValueError(
                f"an app is an app id, the id of its release, and owner/repo names: {exc}"
            ) from None
        return list(chosen.values())

    @field_validator("review_app_preview_checks")
    @classmethod
    def _preview_checks(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        names = list(dict.fromkeys(name.strip() for name in v if name.strip()))
        if any(len(name) > 200 for name in names):
            raise ValueError("a preview check is a name of at most 200 characters")
        return names

    @field_validator("review_browser_domains")
    @classmethod
    def _browser_domains(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        domains = list(dict.fromkeys(d.strip().lower() for d in v if d.strip()))
        for domain in domains:
            if not _DOMAIN.fullmatch(domain):
                raise ValueError(f"{domain!r} isn't a domain, such as review.example.com")
        return domains

    @field_validator("review_browser_session_tool", mode="before")
    @classmethod
    def _session_tool(cls, v: object) -> str | None:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        tool = str(v).strip()
        if not _SESSION_TOOL.fullmatch(tool):
            raise ValueError(
                "the session tool is a tool of Robin's bot kit, such as homebase_sign_in"
            )
        return tool

    @field_validator("org_guidelines", mode="before")
    @classmethod
    def _normalize_review_instructions(cls, v: object) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("review instructions must be a string")
        text = v.strip()
        if not text:
            return None
        if len(text) > ORG_GUIDELINES_MAX_CHARS:
            raise ValueError(
                f"review instructions must be at most {ORG_GUIDELINES_MAX_CHARS} characters"
            )
        return text

    @model_validator(mode="after")
    def _validate_model_pairs(self) -> SettingsUpdate:
        for model_field, effort_field, role in _MODEL_PAIRS:
            _validate_model_effort_pair(
                getattr(self, model_field), getattr(self, effort_field), role
            )
        return self

    def apply_fable_policy(self, *, fable_enabled: bool) -> None:
        """Enforce the Fable rules against the saved toggle."""
        if fable_enabled:
            for model_field, _, _ in _MODEL_PAIRS:
                model = getattr(self, model_field)
                if model in NON_DEFAULT_MODEL_IDS:
                    raise ValueError(f"{model!r} cannot be a default model")
        else:
            # Disabling Fable is the ZDR kill switch and must always succeed: rather
            # than reject a payload that still carries a Fable default, swap each
            # Fable default to its safe non-Fable fallback (mirrors the runtime
            # gate_fable_model guard) so the stored record can't advertise Fable.
            for model_field, effort_field, _ in _MODEL_PAIRS:
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


# Each (model, reasoning effort) pair of fields, and the role validation errors name it by.
_MODEL_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("default_agent_model", "default_agent_reasoning_effort", "agent"),
    ("default_agent_subagent_model", "default_agent_subagent_reasoning_effort", "agent subagent"),
    *(
        (
            f"default_agent_routing_{tier}_model",
            f"default_agent_routing_{tier}_reasoning_effort",
            f"agent routing {tier}",
        )
        for tier in ("fast", "balanced", "performance")
    ),
    ("default_reviewer_model", "default_reviewer_reasoning_effort", "reviewer"),
    (
        "default_reviewer_subagent_model",
        "default_reviewer_subagent_reasoning_effort",
        "reviewer subagent",
    ),
    (
        "default_review_scout_model",
        "default_review_scout_reasoning_effort",
        "review scout",
    ),
    ("default_thread_title_model", "default_thread_title_reasoning_effort", "thread title"),
    ("review_shadow_model", "review_shadow_reasoning_effort", "shadow reviewer"),
)


def normalize_settings_for_response(settings: dict[str, Any]) -> dict[str, Any]:
    value = dict(settings)
    for model_field, effort_field, _ in _MODEL_PAIRS:
        model = value.get(model_field)
        effort = value.get(effort_field)
        if isinstance(model, str):
            value[model_field], value[effort_field] = normalize_model_choice(model, effort)
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
        "implementation_walkthrough_enabled": False,
        "concise_walkthrough_enabled": False,
        "pr_summaries": True,
        "review_trace_links": True,
        "review_apps": [],
        "review_app_preview_checks": [],
        "review_browser_domains": [],
        "review_browser_session_tool": None,
        "featured_apps": [],
        # On by default; the settings show it on until an admin turns it off.
        "model_routing_enabled": True,
        "model_routing_split": False,
        "model_selection_enabled": False,
        "fable_enabled": False,
        "expedited_review_enabled": False,
        "human_review_auto_merge_enabled": True,
        "human_review_auto_assign_minutes": 120,
        "slack_follow_up_suggestions": False,
        "org_guidelines": None,
        "default_agent_model": fallback_model,
        "default_agent_reasoning_effort": fallback_effort,
        "default_agent_subagent_model": fallback_model,
        "default_agent_subagent_reasoning_effort": fallback_effort,
        "default_agent_routing_fast_model": "openai:gpt-6.1-sol",
        "default_agent_routing_fast_reasoning_effort": "medium",
        "default_agent_routing_balanced_model": "openai:gpt-6.1-sol",
        "default_agent_routing_balanced_reasoning_effort": "medium",
        "default_agent_routing_performance_model": "anthropic:claude-opus-5-5",
        "default_agent_routing_performance_reasoning_effort": "high",
        "default_repo": _env_default_repo(),
        "default_reviewer_model": fallback_model,
        "default_reviewer_reasoning_effort": fallback_effort,
        "default_reviewer_subagent_model": fallback_model,
        "default_reviewer_subagent_reasoning_effort": fallback_effort,
        "default_review_scout_model": REVIEW_SCOUT_FALLBACK_MODEL[0],
        "default_review_scout_reasoning_effort": REVIEW_SCOUT_FALLBACK_MODEL[1],
        "default_thread_title_model": DEFAULT_THREAD_TITLE_MODEL,
        "default_thread_title_reasoning_effort": DEFAULT_THREAD_TITLE_REASONING_EFFORT,
        "review_shadow_percent": 0,
        "review_shadow_model": None,
        "review_shadow_reasoning_effort": None,
        "updated_at": None,
    }


_STALE_FIELDS = (
    "gateway_enabled",
    "trigger_mode",
    "autofix_mode",
    "autofix_severity_threshold",
    "autofix_enabled",
    "review_author_context_enabled",
    "review_tracing_project",
    "transcription_model",
    # Approval criteria moved to each repository's .open-swe/auto-approve-rules/ and its review style's mode.
    "approval_policy",
    "review_auto_approve",
    # Every desktop thread runs in the cloud, with approved local actions.
    "cloud_first_desktop_enabled",
)


def _set_fields(record: Mapping[str, Any] | None) -> dict[str, Any]:
    """The fields of a stored record that carry a value.

    None-valued fields fall through to the tier below, so legacy records (or
    PUTs that cleared a selection) never pin a null.
    """
    if not record:
        return {}
    normalized = normalize_settings_for_response(dict(record))
    return {k: v for k, v in normalized.items() if v is not None and k not in _STALE_FIELDS}


def _finish(merged: dict[str, Any]) -> Settings:
    for stale_field in _STALE_FIELDS:
        merged.pop(stale_field, None)
    return Settings(normalize_settings_for_response(merged))


async def instance_record(*, session: AsyncSession | None = None) -> dict[str, Any]:
    """The instance record's set fields, without defaults."""
    return _set_fields(await INSTANCE_SETTINGS.get(session=session))


def fallback_settings() -> Settings:
    """The hardcoded defaults, for when the store cannot be read."""
    return Settings(_default_settings())


async def load_instance_settings() -> Settings:
    """:func:`get_instance_settings`, but raising when the store read fails."""
    return _finish({**_default_settings(), **await instance_record()})


async def get_instance_settings() -> Settings:
    """The global defaults, falling back when storage is unavailable."""
    try:
        return await load_instance_settings()
    except Exception:
        logger.warning("instance settings lookup failed; using defaults", exc_info=True)
        return fallback_settings()


async def get_settings() -> Settings:
    """The global defaults used by every run."""
    return await get_instance_settings()


def _record_values(update: SettingsUpdate) -> dict[str, Any]:
    return {**update.model_dump(), "updated_at": now_iso()}


def _audit_setting_value(field: str, value: object) -> bool | int | Literal["[REDACTED]"] | None:
    if value is None:
        return None
    metadata = SettingsUpdate.model_fields[field].json_schema_extra
    if isinstance(metadata, dict) and metadata.get("agent_feature_flag") and type(value) is bool:
        return value
    if field == "human_review_auto_assign_minutes" and type(value) is int:
        return value
    return "[REDACTED]"


async def _settings_changes(value: Mapping[str, object]) -> dict[str, SettingsChange] | None:
    if current_audit_log.get() is None:
        return None
    try:
        async with asyncio.timeout(2):
            before = await instance_record()
        return {
            field: SettingsChange(
                before=_audit_setting_value(field, before.get(field)),
                after=_audit_setting_value(field, value.get(field)),
            )
            for field in SettingsUpdate.model_fields
            if before.get(field) != value.get(field)
        }
    except Exception:
        logger.warning("Could not read settings for audit changes", exc_info=True)
        return None


def _bind_settings_changes(changes: dict[str, SettingsChange] | None) -> None:
    entry = current_audit_log.get()
    if entry is not None:
        entry.enrichments.settings_scope = "instance"
        entry.enrichments.settings_changes = changes


async def upsert_instance_settings(update: SettingsUpdate) -> dict[str, Any]:
    """Replace the instance record. Raises ``ValueError`` for a Fable model saved as a default."""
    update.apply_fable_policy(fable_enabled=bool(update.fable_enabled))
    value = _record_values(update)
    changes = await _settings_changes(value)
    await INSTANCE_SETTINGS.put("", value)
    _bind_settings_changes(changes)
    await _forget_cached_settings()
    await Topic.SETTINGS.invalidate()
    return value


def _resolve_default_pair(model: object, effort: object) -> tuple[str, str]:
    """Supported pair if valid, else global default."""
    if (
        isinstance(model, str)
        and isinstance(effort, str)
        and model in SUPPORTED_MODEL_IDS
        and model not in NON_DEFAULT_MODEL_IDS
        and model_supports_effort(model, effort)
    ):
        return model, effort
    return default_model_pair()


class ReviewShadowArm(NamedTuple):
    """The share of newly reviewed pull requests shadow-reviewed, and the model that does it."""

    percent: int
    model_id: str
    reasoning_effort: str


class Settings(Mapping[str, Any]):
    """Read-only global defaults and the choices derived from them."""

    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values = dict(values)

    @staticmethod
    async def load(*, session: AsyncSession) -> Settings:
        """Resolve global defaults using the caller's connection."""
        return _finish({**_default_settings(), **await instance_record(session=session)})

    @staticmethod
    async def patch(changes: Mapping[str, object]) -> dict[str, object]:
        """Atomically merge ``changes`` into the instance record, validate, and persist."""
        async with postgres.session() as session:
            before = _set_fields(await INSTANCE_SETTINGS.lock("", session=session))
            update = SettingsUpdate.model_validate({**before, **changes})
            update.apply_fable_policy(fable_enabled=bool(update.fable_enabled))
            value = _record_values(update)
            await INSTANCE_SETTINGS.put("", value, session=session)
        if current_audit_log.get() is not None:
            _bind_settings_changes(
                {
                    field: SettingsChange(
                        before=_audit_setting_value(field, before.get(field)),
                        after=_audit_setting_value(field, value.get(field)),
                    )
                    for field in SettingsUpdate.model_fields
                    if before.get(field) != value.get(field)
                }
            )
        await _forget_cached_settings()
        await Topic.SETTINGS.invalidate()
        return value

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"Settings({self._values!r})"

    @property
    def default_repo(self) -> dict[str, str] | None:
        return _parse_repo(self.get("default_repo"))

    def default_model(self, role: Literal["agent", "reviewer"]) -> tuple[str, str]:
        """The default ``(model_id, reasoning_effort)`` for ``role``.

        Always a valid pair: the configured pair if still supported, otherwise
        the global default from :func:`openswe.dashboard.options.default_model_pair`.
        """
        if role == "agent":
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
    def review_scout_model(self) -> tuple[str, str]:
        """The review scout's ``(model_id, reasoning_effort)``."""
        model = self.get("default_review_scout_model")
        effort = self.get("default_review_scout_reasoning_effort")
        if (
            isinstance(model, str)
            and isinstance(effort, str)
            and model in SUPPORTED_MODEL_IDS
            and model not in NON_DEFAULT_MODEL_IDS
            and model_supports_effort(model, effort)
        ):
            return model, effort
        return REVIEW_SCOUT_FALLBACK_MODEL

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
        return pair

    @property
    def review_shadow(self) -> ReviewShadowArm | None:
        """The shadow review experiment, when it samples any pull requests on a usable model."""
        percent = self.get("review_shadow_percent")
        model = self.get("review_shadow_model")
        effort = self.get("review_shadow_reasoning_effort")
        if (
            type(percent) is not int
            or percent <= 0
            or not isinstance(model, str)
            or not isinstance(effort, str)
            or model not in SUPPORTED_MODEL_IDS
            or not model_supports_effort(model, effort)
        ):
            return None
        return ReviewShadowArm(percent, model, effort)

    @property
    def review_trace_links_enabled(self) -> bool:
        """Whether GitHub review bodies should include a LangSmith trace link."""
        return bool(self.get("review_trace_links", True))

    @property
    def review_app_preview_checks(self) -> frozenset[str]:
        """The checks, by name, whose success means a pull request's preview serves its head."""
        value = self.get("review_app_preview_checks")
        return (
            frozenset(v for v in value if isinstance(v, str))
            if isinstance(value, list)
            else frozenset()
        )

    @property
    def review_app_releases(self) -> dict[UUID, UUID]:
        """Each app whose review step reviews follow, by id, to the release an admin approved,
        in the order they were added."""
        return self._releases("review_apps")

    @property
    def featured_app_releases(self) -> dict[UUID, UUID]:
        """Each app added to everyone's web UI, by id, to the release an admin approved."""
        return self._releases("featured_apps")

    @property
    def featured_app_repositories(self) -> dict[UUID, tuple[str, ...]]:
        """Each app added to everyone's web UI, by id, to the repositories it reads for each
        person who can."""
        value = self.get("featured_apps")
        found: dict[UUID, tuple[str, ...]] = {}
        for entry in value if isinstance(value, list) else []:
            try:
                names = entry.get("repositories") or []
                found[UUID(str(entry["app"]))] = tuple(str(name) for name in names)
            except AttributeError, KeyError, TypeError, ValueError:
                continue
        return found

    def _releases(self, field: str) -> dict[UUID, UUID]:
        value = self.get(field)
        found: dict[UUID, UUID] = {}
        for entry in value if isinstance(value, list) else []:
            try:
                found[UUID(str(entry["app"]))] = UUID(str(entry["release"]))
            except KeyError, TypeError, ValueError:
                continue
        return found

    @property
    def model_routing_enabled(self) -> bool:
        value = self.get("model_routing_enabled")
        return value if isinstance(value, bool) else True

    @property
    def model_routing_split(self) -> bool:
        return self.get("model_routing_split") is True

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
    def human_review_auto_merge_enabled(self) -> bool:
        """Whether standard human-review requests merge automatically by default."""
        value = self.get("human_review_auto_merge_enabled")
        return value if isinstance(value, bool) else True

    @property
    def human_review_auto_assign_minutes(self) -> int:
        value = self.get("human_review_auto_assign_minutes")
        return value if type(value) is int and value > 0 else 120

    @property
    def review_browser_domains(self) -> list[str]:
        """The domains, each with its subdomains, a review's browser may sign in to."""
        value = self.get("review_browser_domains")
        return [d for d in value if isinstance(d, str)] if isinstance(value, list) else []

    @property
    def review_browser_session_tool(self) -> str | None:
        """The bot kit's tool, by its name in the kit, that signs a review's browser in."""
        value = self.get("review_browser_session_tool")
        return value if isinstance(value, str) and value else None

    @property
    def org_review_guidelines(self) -> str | None:
        """The reviewer guidelines supplement, if any."""
        value = self.get("org_guidelines")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None


async def _forget_cached_settings() -> None:
    # The cache module imports this one, so it is imported here.
    from openswe.dashboard.settings_cache import forget_cached_settings

    await forget_cached_settings()


router = APIRouter(tags=["settings"])


@router.get("/settings")
@router.get("/team-settings", include_in_schema=False)
async def api_get_instance_settings(_session: dict[str, Any] = SESSION_DEP) -> dict[str, Any]:
    """The global defaults."""
    return dict(await get_instance_settings())


@router.put("/settings")
@router.put("/team-settings", include_in_schema=False)
@audit_endpoint
async def api_put_instance_settings(
    body: SettingsUpdate, _admin: dict[str, Any] = ADMIN_DEP
) -> dict[str, Any]:
    try:
        await upsert_instance_settings(body)
        return dict(await get_instance_settings())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
