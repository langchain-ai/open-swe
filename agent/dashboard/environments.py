"""Named environments: a prompt plus the scripts that build and boot their sandboxes.

An environment is a definition, not a frozen image. It holds a prompt appended to
the agent's system prompt, a ``setup_script`` that provisions a sandbox from the
base snapshot (clone the repos, install toolchains, warm caches), and an optional
``init_script`` run on every new sandbox booted from the resulting snapshot to
freshen what goes stale in an image.

:mod:`agent.dashboard.environment_refresh` runs both scripts in a throwaway
sandbox and captures the result, nightly on a cron and on demand. Because the
scripts are the definition, the snapshot can always be rebuilt, and the refresh
outcome — status, timestamps, and a capped log — rides on the record for the
dashboard to show.

Snapshots are Docker-style: an environment owns a name (its own, or
``<prefix>-environment-<slug>`` from ``ENVIRONMENT_SNAPSHOT_PREFIX``) and each
refresh publishes under ``name:latest``, moving the tag to the new content. Runs
boot from the immutable snapshot id on the record, not from the tag, so a refresh
mid-run cannot change what a reconnecting sandbox comes back to. The superseded
snapshot is deleted once the new one is ready, so one environment costs one image.

A run uses the environment it selected — from the dashboard picker, or an
``env:<name>`` tag on the Slack message that opened the thread — and otherwise
the one named ``default``. Nothing here is required: with no environment, or one
whose snapshot is not ready, runs fall back to the configured base snapshot.
"""

import base64
import json
import logging
import os
import re
import shlex
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from agent.review.styles import normalize_repo_full_name
from agent.store import TypedStore, now_iso

logger = logging.getLogger(__name__)

ENVIRONMENTS_NAMESPACE: list[str] = ["environments"]
DEFAULT_ENVIRONMENT_SLUG = "default"

SnapshotStatus = Literal["none", "capturing", "ready", "failed"]
RefreshStatus = Literal["never", "refreshing", "success", "failed"]


class SandboxResources(TypedDict, total=False):
    mem_bytes: int
    vcpus: int
    fs_capacity_bytes: int


NAME_MAX_CHARS = 80
PROMPT_MAX_CHARS = 20_000
MAX_REPOS = 50
CREATE_PARAMS_MAX_CHARS = 20_000
SCRIPT_MAX_CHARS = 20_000
REFRESH_LOG_MAX_CHARS = 40_000
SNAPSHOT_NAME_MAX_CHARS = 128
LOG_EXCERPT_LINES = 10
SNAPSHOT_TAG = "latest"

SETUP_SCRIPT_PATH = "/tmp/openswe-environment-setup.sh"  # noqa: S108
INIT_SCRIPT_PATH = "/tmp/openswe-environment-init.sh"  # noqa: S108
DEFAULT_INIT_SCRIPT_TIMEOUT_SECONDS = 2 * 60 * 60

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SENSITIVE_CREATE_PARAM_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "password",
        "pat",
        "private_key",
        "secret",
        "token",
    }
)
_SENSITIVE_CREATE_PARAM_SUFFIXES = (
    "_api_key",
    "_apikey",
    "_authorization",
    "_cookie",
    "_credential",
    "_credentials",
    "_password",
    "_pat",
    "_private_key",
    "_secret",
    "_token",
)
_SENSITIVE_CREATE_PARAM_PREFIXES = (
    "api_key_",
    "authorization_",
    "credential_",
    "credentials_",
    "password_",
    "private_key_",
    "secret_",
    "token_",
)
_SENSITIVE_HEADER_NAMES = frozenset({"authorization", "cookie", "proxy_authorization", "x_api_key"})
# `env:my-box` anywhere in a message, as a whole word.
_ENV_TAG_RE = re.compile(r"(?:(?<=\s)|^)env:([A-Za-z0-9][A-Za-z0-9._-]*)(?=\s|$)")


def slugify(name: str) -> str:
    """Return the storage key for an environment name.

    Also the snapshot name stem, so it is restricted to what a Docker-style tag
    accepts: lowercase alphanumerics and single hyphens.
    """
    slug = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    if not slug:
        raise ValueError("name must contain at least one letter or digit")
    return slug[:NAME_MAX_CHARS]


def snapshot_name_prefix() -> str:
    """Prefix for captured snapshot names, so one workspace can host several deployments.

    A configured prefix carrying a colon would produce a name the platform
    rejects, so it is dropped rather than passed through.
    """
    prefix = os.environ.get("ENVIRONMENT_SNAPSHOT_PREFIX", "").strip()
    if ":" in prefix:
        logger.warning(
            "ENVIRONMENT_SNAPSHOT_PREFIX %r contains a colon, which snapshot names "
            "may not; falling back to the default prefix",
            prefix,
        )
        prefix = ""
    return prefix or "openswe"


def default_snapshot_name_for(slug: str) -> str:
    """``<prefix>-environment-<slug>``: the name an environment publishes under."""
    return f"{snapshot_name_prefix()}-environment-{slug}"


def script_command(script: str, path: str) -> str:
    """Shell command that writes one of an environment's scripts and runs it.

    Base64 so nothing in the script body — quotes, heredocs, newlines — can break
    out of the command carrying it. Output is merged so the caller gets one log.
    """
    encoded = base64.b64encode(script.encode()).decode()
    return f"printf %s {shlex.quote(encoded)} | base64 -d > {path} && bash {path} 2>&1"


def init_script_timeout() -> int:
    """Deadline for the per-sandbox init script, which blocks the first model call."""
    raw = os.environ.get("ENVIRONMENT_INIT_SCRIPT_TIMEOUT_SECONDS", "").strip()
    try:
        value = int(raw) if raw else 0
    except ValueError:
        value = 0
    return value if value > 0 else DEFAULT_INIT_SCRIPT_TIMEOUT_SECONDS


def log_excerpt(log: str | None, *, lines: int = LOG_EXCERPT_LINES) -> str | None:
    """Head and tail of a refresh log, for readers who should not see all of it.

    A setup script prints whatever it prints, so the middle — dependency
    resolution, compiler chatter — is both the bulk and the least useful part.
    """
    if not log or not log.strip():
        return None
    entries = log.strip().splitlines()
    if len(entries) <= lines * 2:
        return "\n".join(entries)
    omitted = len(entries) - lines * 2
    return "\n".join([*entries[:lines], f"… {omitted} lines omitted …", *entries[-lines:]])


def _validate_name(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError("name must not be empty")
    if len(text) > NAME_MAX_CHARS:
        raise ValueError(f"name must be at most {NAME_MAX_CHARS} characters")
    slugify(text)
    return text


def _validate_prompt(value: str | None) -> str:
    text = (value or "").strip()
    if len(text) > PROMPT_MAX_CHARS:
        raise ValueError(f"prompt must be at most {PROMPT_MAX_CHARS} characters")
    return text


def _validate_script(value: str | None) -> str:
    text = (value or "").strip()
    if len(text) > SCRIPT_MAX_CHARS:
        raise ValueError(f"script must be at most {SCRIPT_MAX_CHARS} characters")
    return text


def _validate_snapshot_name(value: str | None) -> str | None:
    """A colon would be read as the ``name:tag`` separator, so the name may not carry one."""
    text = (value or "").strip()
    if not text:
        return None
    if ":" in text:
        raise ValueError("snapshot_name must not contain a colon")
    if len(text) > SNAPSHOT_NAME_MAX_CHARS:
        raise ValueError(f"snapshot_name must be at most {SNAPSHOT_NAME_MAX_CHARS} characters")
    return text


def _validate_repos(value: list[str] | None) -> list[str]:
    if not value:
        return []
    if len(value) > MAX_REPOS:
        raise ValueError(f"at most {MAX_REPOS} repositories per environment")
    return list(dict.fromkeys(normalize_repo_full_name(entry) for entry in value))


def _normalize_create_param_name(value: str) -> str:
    snake_value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value.strip())
    return re.sub(r"[^a-z0-9]+", "_", snake_value.lower()).strip("_")


def _is_sensitive_create_param_name(value: str) -> bool:
    normalized = _normalize_create_param_name(value)
    return (
        normalized in _SENSITIVE_CREATE_PARAM_KEYS
        or normalized.endswith(_SENSITIVE_CREATE_PARAM_SUFFIXES)
        or normalized.startswith(_SENSITIVE_CREATE_PARAM_PREFIXES)
    )


def _has_sensitive_create_param(value: JsonValue) -> bool:
    if isinstance(value, dict):
        header_name = value.get("name")
        if isinstance(header_name, str):
            normalized_header = _normalize_create_param_name(header_name)
            if normalized_header in _SENSITIVE_HEADER_NAMES or _is_sensitive_create_param_name(
                normalized_header
            ):
                return True
        for key, nested in value.items():
            if _is_sensitive_create_param_name(key):
                return True
            if _has_sensitive_create_param(nested):
                return True
    elif isinstance(value, list):
        return any(_has_sensitive_create_param(item) for item in value)
    return False


def _validate_create_params(value: dict[str, JsonValue] | None) -> dict[str, JsonValue]:
    params = value or {}
    proxy_config = params.get("proxy_config")
    if proxy_config is not None:
        if not isinstance(proxy_config, dict):
            raise ValueError("create_params.proxy_config must be a JSON object")
        if "rules" in proxy_config and not isinstance(proxy_config["rules"], list):
            raise ValueError("create_params.proxy_config.rules must be a JSON array")
    try:
        serialized = json.dumps(params, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("create_params must contain only valid JSON values") from exc
    if len(serialized) > CREATE_PARAMS_MAX_CHARS:
        raise ValueError(f"create_params must be at most {CREATE_PARAMS_MAX_CHARS} JSON characters")
    if _has_sensitive_create_param(params):
        raise ValueError("create_params must not contain secrets or authentication credentials")
    return params


class EnvironmentCreate(BaseModel):
    name: str
    prompt: str = ""
    setup_script: str = ""
    init_script: str = ""
    base_snapshot_id: str | None = None
    snapshot_name: str | None = None
    repos: list[str] = Field(default_factory=list)
    mem_bytes: int | None = Field(default=None, gt=0)
    vcpus: int | None = Field(default=None, gt=0)
    fs_capacity_bytes: int | None = Field(default=None, gt=0)
    create_params: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        return _validate_name(v)

    @field_validator("prompt")
    @classmethod
    def _check_prompt(cls, v: str) -> str:
        return _validate_prompt(v)

    @field_validator("setup_script", "init_script")
    @classmethod
    def _check_script(cls, v: str) -> str:
        return _validate_script(v)

    @field_validator("base_snapshot_id")
    @classmethod
    def _check_base_snapshot_id(cls, v: str | None) -> str | None:
        return (v or "").strip() or None

    @field_validator("snapshot_name")
    @classmethod
    def _check_snapshot_name(cls, v: str | None) -> str | None:
        return _validate_snapshot_name(v)

    @field_validator("repos")
    @classmethod
    def _check_repos(cls, v: list[str]) -> list[str]:
        return _validate_repos(v)

    @field_validator("create_params")
    @classmethod
    def _check_create_params(cls, v: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _validate_create_params(v)


class EnvironmentUpdate(BaseModel):
    """Partial update: only the fields present are written."""

    name: str | None = None
    prompt: str | None = None
    setup_script: str | None = None
    init_script: str | None = None
    base_snapshot_id: str | None = None
    snapshot_name: str | None = None
    repos: list[str] | None = None
    mem_bytes: int | None = Field(default=None, gt=0)
    vcpus: int | None = Field(default=None, gt=0)
    fs_capacity_bytes: int | None = Field(default=None, gt=0)
    create_params: dict[str, JsonValue] | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str | None) -> str | None:
        return None if v is None else _validate_name(v)

    @field_validator("prompt")
    @classmethod
    def _check_prompt(cls, v: str | None) -> str | None:
        return None if v is None else _validate_prompt(v)

    @field_validator("setup_script", "init_script")
    @classmethod
    def _check_script(cls, v: str | None) -> str | None:
        return None if v is None else _validate_script(v)

    @field_validator("snapshot_name")
    @classmethod
    def _check_snapshot_name(cls, v: str | None) -> str | None:
        return _validate_snapshot_name(v)

    @field_validator("repos")
    @classmethod
    def _check_repos(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _validate_repos(v)

    @field_validator("create_params")
    @classmethod
    def _check_create_params(cls, v: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
        return None if v is None else _validate_create_params(v)


class Environment(BaseModel):
    # Assignment is validated because the store mutates records in place, and an
    # unvalidated write here is only caught on the next read — by which point the
    # record is already unreadable.
    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    slug: str
    name: str = ""
    prompt: str = ""
    setup_script: str = ""
    init_script: str = ""
    base_snapshot_id: str | None = None
    repos: list[str] = Field(default_factory=list)
    mem_bytes: int | None = None
    vcpus: int | None = None
    fs_capacity_bytes: int | None = None
    create_params: dict[str, JsonValue] = Field(default_factory=dict)
    snapshot_id: str | None = None
    snapshot_name: str | None = None
    snapshot_status: SnapshotStatus = "none"
    status_message: str | None = None
    snapshot_tag: str | None = None
    # The init script as it stood when the live snapshot was captured. `init_script`
    # is the desired one and may be unvalidated; this is the one that shipped.
    validated_init_script: str = ""
    source_sandbox_id: str | None = None
    last_captured_at: str | None = None
    refresh_status: RefreshStatus = "never"
    refresh_started_at: str | None = None
    refresh_finished_at: str | None = None
    refresh_log: str | None = None
    refresh_error: str | None = None
    refresh_cron_id: str | None = None
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""

    @field_validator("create_params", mode="before")
    @classmethod
    def _null_create_params_are_empty(cls, v: Any) -> Any:
        """``EnvironmentUpdate`` clears create params with an explicit null."""
        return {} if v is None else v

    @field_validator("setup_script", "init_script", mode="before")
    @classmethod
    def _scripts_are_stripped(cls, v: Any) -> Any:
        """Stripped on the way in, so ``if record.setup_script`` is the whole test."""
        return v.strip() if isinstance(v, str) else ("" if v is None else v)

    @classmethod
    def seed(cls, create: EnvironmentCreate, created_by: str) -> "Environment":
        now = now_iso()
        return cls(
            slug=slugify(create.name),
            name=create.name.strip(),
            prompt=create.prompt,
            setup_script=create.setup_script,
            init_script=create.init_script,
            base_snapshot_id=create.base_snapshot_id,
            snapshot_name=create.snapshot_name or default_snapshot_name_for(slugify(create.name)),
            repos=create.repos,
            mem_bytes=create.mem_bytes,
            vcpus=create.vcpus,
            fs_capacity_bytes=create.fs_capacity_bytes,
            create_params=create.create_params,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )

    @property
    def ready_snapshot_id(self) -> str | None:
        """The snapshot new sandboxes boot from, or ``None`` when not captured yet.

        A capture in flight still serves the previous snapshot: the new id is
        written only once the capture succeeds, so the old one stays valid
        throughout. Dropping it here would send every run started during a
        nightly refresh to the bare base image.
        """
        if self.snapshot_status not in ("ready", "capturing"):
            return None
        return self.snapshot_id or None

    @property
    def instructions(self) -> str | None:
        return self.prompt.strip() or None

    @property
    def published_snapshot_name(self) -> str:
        """The name this environment publishes under, stored or derived.

        Stable for the life of the environment: every refresh re-captures under
        it and moves the tag, so the name is an address callers can hold.
        """
        return self.snapshot_name or default_snapshot_name_for(self.slug)

    def sandbox_resources(self) -> SandboxResources:
        """VM sizing as sandbox-create kwargs, omitting anything unset."""
        resources: SandboxResources = {}
        for field in ("mem_bytes", "vcpus", "fs_capacity_bytes"):
            value = getattr(self, field)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                resources[field] = value
        return resources

    def sandbox_create_params(self) -> dict[str, JsonValue]:
        """Validated passthrough create-body fields.

        Re-validated on read, not just on write: the rules (size cap, no
        secrets) can tighten after a record was stored, and shipping a stale
        record's params to the platform would bypass the newer rule.
        """
        try:
            return _validate_create_params(self.create_params)
        except ValueError:
            logger.warning("Ignoring invalid sandbox create params for environment %s", self.slug)
            return {}

    def option(self) -> dict[str, Any]:
        """Name/slug/snapshot-state only, for the non-admin environment picker."""
        return {
            "slug": self.slug,
            "name": self.name,
            "has_snapshot": self.snapshot_status == "ready",
            "refresh_status": self.refresh_status,
            "refresh_finished_at": self.refresh_finished_at,
            "refresh_error": self.refresh_error,
            "refresh_log_excerpt": log_excerpt(self.refresh_log),
        }


class EnvironmentStore(TypedStore[Environment]):
    def __init__(self) -> None:
        super().__init__(ENVIRONMENTS_NAMESPACE, Environment)

    async def list_all(self) -> list[Environment]:
        records = await self.search_all()
        records.sort(key=lambda record: record.name)
        return records

    async def save(self, record: Environment) -> Environment:
        record.updated_at = now_iso()
        return await self.put(record.slug, record)

    async def create(self, create: EnvironmentCreate, created_by: str) -> Environment:
        record = Environment.seed(create, created_by)
        if await self.get(record.slug) is not None:
            raise ValueError(f"environment {create.name!r} already exists")
        return await self.put(record.slug, record)

    async def apply_update(self, slug: str, update: EnvironmentUpdate) -> Environment:
        record = await self.get(slug)
        if record is None:
            raise ValueError(f"no environment named {slug!r}")
        if update.name is not None and slugify(update.name) != slug:
            raise ValueError(
                "renaming an environment across slugs is not supported; create a new one"
            )
        if update.name is not None:
            record.name = update.name.strip()
        if update.prompt is not None:
            record.prompt = update.prompt
        if update.repos is not None:
            record.repos = update.repos
        if update.setup_script is not None:
            record.setup_script = update.setup_script
        if update.init_script is not None:
            record.init_script = update.init_script
        for field in (
            "mem_bytes",
            "vcpus",
            "fs_capacity_bytes",
            "create_params",
            "base_snapshot_id",
            "snapshot_name",
        ):
            if field in update.model_fields_set:
                setattr(record, field, getattr(update, field))
        return await self.save(record)

    async def remove(self, slug: str) -> bool:
        record = await self.get(slug)
        if record is None:
            return False
        await self.delete(slug)
        from agent.dashboard.environment_refresh import remove_refresh_cron

        await remove_refresh_cron(record)
        await _delete_snapshot(record.snapshot_id)
        return True

    async def mark_capturing(self, slug: str) -> Environment | None:
        record = await self.get(slug)
        if record is None:
            return None
        record.snapshot_status = "capturing"
        record.status_message = None
        return await self.save(record)

    async def mark_capture_settled(
        self, slug: str, status: SnapshotStatus, message: str
    ) -> Environment | None:
        """Land a failed capture on ``status``, keeping a previously ready snapshot."""
        record = await self.get(slug)
        if record is None:
            return None
        record.snapshot_status = status
        record.status_message = message
        return await self.save(record)

    async def mark_captured(
        self,
        slug: str,
        *,
        snapshot_id: str,
        snapshot_name: str,
        source_sandbox_id: str,
        snapshot_tag: str = SNAPSHOT_TAG,
    ) -> Environment | None:
        record = await self.get(slug)
        if record is None:
            return None
        record.snapshot_status = "ready"
        record.status_message = None
        record.snapshot_id = snapshot_id
        record.snapshot_name = snapshot_name
        record.snapshot_tag = snapshot_tag
        record.validated_init_script = record.init_script
        record.source_sandbox_id = source_sandbox_id
        record.last_captured_at = now_iso()
        return await self.save(record)

    async def mark_refreshing(self, slug: str) -> Environment | None:
        record = await self.get(slug)
        if record is None:
            return None
        record.refresh_status = "refreshing"
        record.refresh_started_at = now_iso()
        record.refresh_finished_at = None
        record.refresh_log = None
        record.refresh_error = None
        return await self.save(record)

    async def mark_refresh_settled(
        self,
        slug: str,
        status: RefreshStatus,
        *,
        log: str | None = None,
        error: str | None = None,
    ) -> Environment | None:
        """Record how the last rebuild went, without touching snapshot state.

        Snapshot state answers "is there something to boot from"; this answers
        "did the last rebuild work". A failed refresh leaves the previous
        snapshot ``ready``, so the two must not share a field.
        """
        record = await self.get(slug)
        if record is None:
            return None
        record.refresh_status = status
        record.refresh_finished_at = now_iso()
        record.refresh_log = (log or "")[-REFRESH_LOG_MAX_CHARS:] or None
        record.refresh_error = error[:1000] if error else None
        return await self.save(record)


ENVIRONMENTS = EnvironmentStore()


async def resolve_default_environment() -> Environment | None:
    """Return the environment named ``default``, or ``None``.

    Fail-soft on purpose: this runs while a sandbox is being created, and a
    store failure must fall back to the base snapshot with no environment
    prompt rather than fail the run.
    """
    try:
        return await ENVIRONMENTS.get(DEFAULT_ENVIRONMENT_SLUG)
    except Exception:
        logger.warning("default environment resolution failed", exc_info=True)
        return None


async def resolve_environment(slug: str | None) -> Environment | None:
    """Return the environment a run uses: the one it selected, else ``default``.

    Never raises, and a selection that no longer exists falls back to ``default``
    rather than failing the run.
    """
    if not slug or slug == DEFAULT_ENVIRONMENT_SLUG:
        return await resolve_default_environment()
    try:
        record = await ENVIRONMENTS.get(slug)
    except Exception:
        logger.warning("environment resolution failed for %s", slug, exc_info=True)
        record = None
    if record is None:
        logger.info("Environment %s is not configured; falling back to the default", slug)
        return await resolve_default_environment()
    return record


async def list_environment_options() -> list[dict[str, Any]]:
    """Name/slug/snapshot-state only, for the non-admin environment picker.

    Prompts and snapshot ids stay admin-only; picking an environment needs
    neither.
    """
    return [record.option() for record in await ENVIRONMENTS.list_all()]


def parse_environment_tag(text: str) -> tuple[str | None, str]:
    """Split a leading-or-inline ``env:<name>`` tag off a message.

    Returns ``(slug, text_without_the_tag)``; ``(None, text)`` when there is no
    tag. The caller decides whether the slug names a real environment — an
    unresolvable tag should be left in the text rather than silently dropped.
    """
    match = _ENV_TAG_RE.search(text or "")
    if match is None:
        return None, text
    try:
        slug = slugify(match.group(1))
    except ValueError:
        return None, text
    before, after = text[: match.start()].rstrip(), text[match.end() :].lstrip()
    return slug, f"{before} {after}".strip() if before and after else f"{before}{after}".strip()


def require_capture_support() -> None:
    """Only the langsmith provider has a snapshot API to capture into."""
    sandbox_type = os.getenv("SANDBOX_TYPE", "langsmith")
    if sandbox_type != "langsmith":
        raise RuntimeError(
            f"capturing an environment snapshot needs SANDBOX_TYPE=langsmith, not {sandbox_type!r}"
        )


async def _delete_snapshot(snapshot_id: object) -> None:
    """Best-effort delete of a superseded snapshot."""
    if not isinstance(snapshot_id, str) or not snapshot_id:
        return
    from agent.sandboxes.providers.langsmith import get_async_sandbox_client

    try:
        async with get_async_sandbox_client() as client:
            await client.delete_snapshot(snapshot_id)
    except Exception:  # noqa: BLE001
        logger.warning("failed to delete superseded snapshot %s", snapshot_id, exc_info=True)


async def capture_environment_snapshot(
    slug: str,
    sandbox_id: str,
    *,
    timeout: int = 600,
) -> Environment:
    """Capture ``sandbox_id``'s filesystem as this environment's ``name:tag``.

    The name belongs to the environment and never moves; each capture publishes
    new content under it and the tag is repointed, which is why nothing here
    handles a name collision — re-using a tag is the documented way to move it.

    The previous snapshot survives a failed capture, in both senses: it is deleted
    only once the new one is ready, and the record stays ``ready`` so runs keep
    booting from it instead of dropping to the base image.

    Only the langsmith provider can capture; other providers have no snapshot API
    to capture into, so this raises rather than failing deep in the SDK.
    """
    from agent.sandboxes.providers.langsmith import (
        capture_snapshot_with_tag,
        get_async_sandbox_client,
    )

    require_capture_support()

    record = await ENVIRONMENTS.get(slug)
    if record is None:
        raise ValueError(f"no environment named {slug!r}")

    snapshot_name = record.published_snapshot_name
    previous_snapshot_id = record.snapshot_id
    previous_was_ready = record.ready_snapshot_id is not None
    await ENVIRONMENTS.mark_capturing(slug)
    try:
        async with get_async_sandbox_client() as client:
            snapshot = await capture_snapshot_with_tag(
                client, sandbox_id, snapshot_name, SNAPSHOT_TAG, timeout=timeout
            )
    except Exception as exc:
        logger.warning("snapshot capture failed for environment %s", slug, exc_info=True)
        await ENVIRONMENTS.mark_capture_settled(
            slug,
            "ready" if previous_was_ready else "failed",
            str(exc)[:1000],
        )
        raise

    updated = await ENVIRONMENTS.mark_captured(
        slug,
        snapshot_id=snapshot.id,
        snapshot_name=snapshot_name,
        snapshot_tag=SNAPSHOT_TAG,
        source_sandbox_id=sandbox_id,
    )
    # Re-read before deleting: a concurrent refresh may have captured and pointed
    # the record at its own snapshot, and deleting ours would strand it.
    current = await ENVIRONMENTS.get(slug)
    if (
        previous_snapshot_id != snapshot.id
        and current is not None
        and current.snapshot_id == snapshot.id
    ):
        await _delete_snapshot(previous_snapshot_id)
    logger.info(
        "Captured snapshot %s as %s:%s for environment %s",
        snapshot.id,
        snapshot_name,
        SNAPSHOT_TAG,
        slug,
    )
    return updated or record
