"""Named environments: a prompt plus the scripts that build and boot their sandboxes.

An environment is a definition, not a frozen image. It holds a prompt appended to
the agent's system prompt, a ``setup_script`` that provisions a sandbox from the
base snapshot (clone the repos, install toolchains, warm caches), and an optional
``update_script`` that freshens what goes stale in an image — a ``git pull``, a
dependency sync — run against the current snapshot at most hourly, and only while
the environment is actually in use.

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
import re
import shlex
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from agent.config import ENV
from agent.review.styles import normalize_repo_full_name
from agent.store import TypedStore, now_iso

logger = logging.getLogger(__name__)

ENVIRONMENTS_NAMESPACE: list[str] = ["environments"]
DEFAULT_ENVIRONMENT_SLUG = "default"

SnapshotStatus = Literal["none", "capturing", "ready", "failed"]
RefreshStatus = Literal["never", "refreshing", "success", "failed"]
RefreshKind = Literal["full", "update"]
StepStatus = Literal["running", "success", "failed"]


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

# Scripts and their logs live under a fixed root that is captured with the
# snapshot, so any sandbox booted from an image carries the log of the run that
# produced it — readable in place, without the dashboard.
DEFAULT_SCRIPT_ROOT = "/open-swe/environment"
DEFAULT_SANDBOX_UPDATE_TIMEOUT_SECONDS = 120

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
    prefix = ENV.ENVIRONMENT_SNAPSHOT_PREFIX.get("").strip()
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


def script_root() -> str:
    """Where an environment's scripts and their logs live inside a sandbox.

    ``/open-swe/environment`` in a real sandbox, where the agent runs as root.
    Configurable
    because ``SANDBOX_TYPE=local`` executes on a developer's own machine, whose
    filesystem root is not writable.
    """
    return ENV.OPENSWE_SCRIPT_ROOT.get().strip().rstrip("/") or DEFAULT_SCRIPT_ROOT


def script_log_path(label: str) -> str:
    """Canonical log path for a script, inside the sandbox and in the snapshot."""
    return f"{script_root()}/logs/{label}.log"


def script_log_paths() -> dict[str, str]:
    """Every script log path, for handing to a caller that wants to read them later.

    These are paths *inside a sandbox*. A refresh writes them on its own
    throwaway builder, which is reclaimed once the capture lands — so they are
    not readable from the thread that started the refresh. They are captured
    into the snapshot, so any sandbox booted from it afterwards has them.
    """
    return {label: script_log_path(label) for label in ("setup", "update")}


def script_command(script: str, label: str) -> str:
    """Shell command that writes one of an environment's scripts, runs it, and logs it.

    Base64 so nothing in the script body — quotes, heredocs, newlines — can break
    out of the command carrying it.

    ``bash -x`` traces each command into the log, which is what makes it useful
    after the fact: a hung or half-finished provision shows the exact step it
    reached. Note that the trace expands arguments, so a script that puts a
    credential on a command line would write it here — scripts must not carry
    secrets, and the proxy injects git auth precisely so they do not have to.

    The log is written to ``<root>/logs/<label>.log`` inside the sandbox *and*
    echoed back, so the caller records it while the file rides along into the
    snapshot. The exit code is the script's own, not ``cat``'s.
    """
    root = script_root()
    path = f"{root}/{label}.sh"
    log_path = script_log_path(label)
    encoded = base64.b64encode(script.encode()).decode()
    return (
        f"mkdir -p {shlex.quote(f'{root}/logs')} "
        f"&& printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(path)} "
        f"&& {{ bash -x {shlex.quote(path)} > {shlex.quote(log_path)} 2>&1; }}; "
        f"rc=$?; cat {shlex.quote(log_path)} 2>/dev/null; exit $rc"
    )


def sandbox_update_timeout() -> int:
    """Deadline for the update script when it runs in a run's own sandbox.

    Tighter than the builder's: this one is on the critical path before the first
    model call, and a ``git pull`` that takes minutes is broken rather than slow.
    """
    seconds = ENV.ENVIRONMENT_SANDBOX_UPDATE_TIMEOUT_SECONDS.get_int(
        DEFAULT_SANDBOX_UPDATE_TIMEOUT_SECONDS
    )
    return seconds if seconds > 0 else DEFAULT_SANDBOX_UPDATE_TIMEOUT_SECONDS


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
    update_script: str = ""
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

    @field_validator("setup_script", "update_script")
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
    update_script: str | None = None
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

    @field_validator("setup_script", "update_script")
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


class RefreshStep(BaseModel):
    """One stage of a refresh — booting the builder, a script, the capture.

    Recorded as it happens so a poll mid-rebuild can say how far it got. A
    rebuild is minutes to an hour, and a single terminal status at the end is
    not enough to tell slow from wedged.
    """

    model_config = ConfigDict(extra="ignore")

    label: str
    status: StepStatus = "running"
    started_at: str = ""
    finished_at: str | None = None
    exit_code: int | None = None
    log_path: str | None = None


class Environment(BaseModel):
    # Assignment is validated because the store mutates records in place, and an
    # unvalidated write here is only caught on the next read — by which point the
    # record is already unreadable.
    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    slug: str
    name: str = ""
    prompt: str = ""
    setup_script: str = ""
    update_script: str = ""
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
    source_sandbox_id: str | None = None
    last_captured_at: str | None = None
    refresh_status: RefreshStatus = "never"
    refresh_kind: RefreshKind | None = None
    refresh_run_id: str | None = None
    refresh_started_at: str | None = None
    refresh_finished_at: str | None = None
    refresh_log: str | None = None
    refresh_error: str | None = None
    refresh_cron_id: str | None = None
    refresh_steps: list[RefreshStep] = Field(default_factory=list)
    # The builder, while it lives: a poll reads the running script's trace off it.
    refresh_sandbox_id: str | None = None
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""

    @field_validator("create_params", mode="before")
    @classmethod
    def _null_create_params_are_empty(cls, v: Any) -> Any:
        """``EnvironmentUpdate`` clears create params with an explicit null."""
        return {} if v is None else v

    @field_validator("setup_script", "update_script", mode="before")
    @classmethod
    def _scripts_are_stripped(cls, v: Any) -> Any:
        """Stripped on the way in, so ``if record.setup_script`` is the whole test."""
        return v.strip() if isinstance(v, str) else ("" if v is None else v)

    @classmethod
    def seed(cls, create: EnvironmentCreate, created_by: str) -> Environment:
        now = now_iso()
        return cls(
            slug=slugify(create.name),
            name=create.name.strip(),
            prompt=create.prompt,
            setup_script=create.setup_script,
            update_script=create.update_script,
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

    def option(self, *, include_log: bool = False) -> dict[str, Any]:
        """Name/slug/refresh-state for the environment picker and the settings page.

        The log excerpt is admin-only: scripts run under ``bash -x``, whose trace
        expands every argument, so a script that put a credential on a command
        line has written it here.
        """
        option = {
            "slug": self.slug,
            "name": self.name,
            "has_snapshot": self.snapshot_status == "ready",
            "refresh_status": self.refresh_status,
            "refresh_kind": self.refresh_kind,
            "refresh_finished_at": self.refresh_finished_at,
            "refresh_error": self.refresh_error,
            "refresh_steps": [step.model_dump(mode="json") for step in self.refresh_steps],
        }
        if include_log:
            option["refresh_log_excerpt"] = log_excerpt(self.refresh_log)
        return option


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
        return await self.save(_apply(record, update))

    async def publish(
        self,
        slug: str,
        definition: EnvironmentCreate | EnvironmentUpdate,
        *,
        snapshot_id: str,
        snapshot_name: str,
        source_sandbox_id: str,
        created_by: str,
    ) -> Environment:
        """Write a definition and the image it was captured from as one store write.

        The image already exists by the time this runs; what must not happen is a
        record that carries the new definition but still points at the old image,
        or a new environment with no image at all. One ``put`` cannot land half.
        """
        if isinstance(definition, EnvironmentCreate):
            if await self.get(slug) is not None:
                raise ValueError(f"environment {definition.name!r} already exists")
            record = Environment.seed(definition, created_by)
        else:
            existing = await self.get(slug)
            if existing is None:
                raise ValueError(f"no environment named {slug!r}")
            record = _apply(existing, definition)
        _stamp_captured(
            record,
            snapshot_id=snapshot_id,
            snapshot_name=snapshot_name,
            source_sandbox_id=source_sandbox_id,
        )
        return await self.save(record)

    async def remove(self, slug: str) -> bool:
        record = await self.get(slug)
        if record is None:
            return False
        from agent.dashboard.environment_auth import delete_environment_auth

        await delete_environment_auth(slug)
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
        _stamp_captured(
            record,
            snapshot_id=snapshot_id,
            snapshot_name=snapshot_name,
            source_sandbox_id=source_sandbox_id,
            snapshot_tag=snapshot_tag,
        )
        return await self.save(record)

    async def mark_refreshing(self, slug: str, kind: RefreshKind = "full") -> Environment | None:
        record = await self.get(slug)
        if record is None:
            return None
        record.refresh_status = "refreshing"
        record.refresh_kind = kind
        record.refresh_started_at = now_iso()
        record.refresh_finished_at = None
        record.refresh_log = None
        record.refresh_error = None
        record.refresh_steps = []
        record.refresh_sandbox_id = None
        return await self.save(record)

    async def start_refresh_step(
        self, slug: str, label: str, *, log_path: str | None = None
    ) -> Environment | None:
        """Open a step, replacing any earlier one with the same label."""
        record = await self.get(slug)
        if record is None:
            return None
        record.refresh_steps = [
            *(step for step in record.refresh_steps if step.label != label),
            RefreshStep(label=label, started_at=now_iso(), log_path=log_path),
        ]
        return await self.save(record)

    async def finish_refresh_step(
        self, slug: str, label: str, status: StepStatus, *, exit_code: int | None = None
    ) -> Environment | None:
        record = await self.get(slug)
        if record is None:
            return None
        record.refresh_steps = [
            step.model_copy(
                update={"status": status, "finished_at": now_iso(), "exit_code": exit_code}
            )
            if step.label == label
            else step
            for step in record.refresh_steps
        ]
        return await self.save(record)

    async def mark_refresh_builder(self, slug: str, sandbox_id: str | None) -> Environment | None:
        """Publish (or clear) the builder a poll may read the live trace from."""
        record = await self.get(slug)
        if record is None:
            return None
        record.refresh_sandbox_id = sandbox_id
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
        # The builder is released with the refresh, so its id stops being a
        # readable source the moment this lands.
        record.refresh_sandbox_id = None
        record.refresh_steps = [
            step.model_copy(update={"status": "failed", "finished_at": now_iso()})
            if step.status == "running"
            else step
            for step in record.refresh_steps
        ]
        return await self.save(record)


def _apply(record: Environment, update: EnvironmentUpdate) -> Environment:
    """Apply a partial update in memory; only the fields present are written."""
    if update.name is not None and slugify(update.name) != record.slug:
        raise ValueError("renaming an environment across slugs is not supported; create a new one")
    if update.name is not None:
        record.name = update.name.strip()
    if update.prompt is not None:
        record.prompt = update.prompt
    if update.repos is not None:
        record.repos = update.repos
    if update.setup_script is not None:
        record.setup_script = update.setup_script
    if update.update_script is not None:
        record.update_script = update.update_script
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
    return record


def _stamp_captured(
    record: Environment,
    *,
    snapshot_id: str,
    snapshot_name: str,
    source_sandbox_id: str,
    snapshot_tag: str = SNAPSHOT_TAG,
) -> None:
    record.snapshot_status = "ready"
    record.status_message = None
    record.snapshot_id = snapshot_id
    record.snapshot_name = snapshot_name
    record.snapshot_tag = snapshot_tag
    record.source_sandbox_id = source_sandbox_id
    record.last_captured_at = now_iso()


ENVIRONMENTS = EnvironmentStore()


async def resolve_default_environment(*, fail_on_error: bool = False) -> Environment | None:
    """Return the environment named ``default``, or ``None``.

    Fail-soft on purpose: this runs while a sandbox is being created, and a
    store failure must fall back to the base snapshot with no environment
    prompt rather than fail the run. Runtime paths that also resolve environment
    credentials opt into strict failure handling.
    """
    try:
        return await ENVIRONMENTS.get(DEFAULT_ENVIRONMENT_SLUG)
    except Exception:
        if fail_on_error:
            raise
        logger.warning("default environment resolution failed", exc_info=True)
        return None


async def resolve_environment(
    slug: str | None, *, fail_on_error: bool = False
) -> Environment | None:
    """Return the environment a run uses: the one it selected, else ``default``.

    By default, a selection that no longer exists or cannot be read falls back
    to ``default``. Runtime paths that resolve credentials request strict errors.
    """
    if not slug or slug == DEFAULT_ENVIRONMENT_SLUG:
        return await resolve_default_environment(fail_on_error=fail_on_error)
    try:
        record = await ENVIRONMENTS.get(slug)
    except Exception:
        if fail_on_error:
            raise
        logger.warning("environment resolution failed for %s", slug, exc_info=True)
        record = None
    if record is None:
        logger.info("Environment %s is not configured; falling back to the default", slug)
        return await resolve_default_environment(fail_on_error=fail_on_error)
    return record


async def list_environment_options(*, include_logs: bool = False) -> list[dict[str, Any]]:
    """Every environment's picker/settings view; ``include_logs`` only for admins.

    Prompts and snapshot ids never appear here; picking an environment needs
    neither.
    """
    return [record.option(include_log=include_logs) for record in await ENVIRONMENTS.list_all()]


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
    sandbox_type = ENV.SANDBOX_TYPE.get()
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
    require_capture_support()

    record = await ENVIRONMENTS.get(slug)
    if record is None:
        raise ValueError(f"no environment named {slug!r}")

    snapshot_name = record.published_snapshot_name
    previous_snapshot_id = record.snapshot_id
    previous_was_ready = record.ready_snapshot_id is not None
    await ENVIRONMENTS.mark_capturing(slug)
    try:
        snapshot_id = await capture_sandbox_snapshot(sandbox_id, snapshot_name, timeout=timeout)
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
        snapshot_id=snapshot_id,
        snapshot_name=snapshot_name,
        snapshot_tag=SNAPSHOT_TAG,
        source_sandbox_id=sandbox_id,
    )
    await retire_superseded_snapshot(slug, previous_snapshot_id, snapshot_id)
    return updated or record


async def capture_sandbox_snapshot(sandbox_id: str, snapshot_name: str, *, timeout: int) -> str:
    """Capture ``sandbox_id`` as ``snapshot_name:latest`` and return the new snapshot id.

    Touches no record: callers that must not write anything until the image
    exists — publishing an environment from a live sandbox — capture first and
    record second.
    """
    from agent.sandboxes.providers.langsmith import (
        capture_snapshot_with_tag,
        get_async_sandbox_client,
    )

    require_capture_support()
    async with get_async_sandbox_client() as client:
        snapshot = await capture_snapshot_with_tag(
            client, sandbox_id, snapshot_name, SNAPSHOT_TAG, timeout=timeout
        )
    logger.info(
        "Captured snapshot %s as %s:%s from sandbox %s",
        snapshot.id,
        snapshot_name,
        SNAPSHOT_TAG,
        sandbox_id,
    )
    return str(snapshot.id)


async def discard_unreferenced_snapshot(slug: str, snapshot_id: str) -> None:
    """Delete a snapshot that was captured but never made it onto the record.

    Re-reads first so a capture that *did* land — by this caller or a concurrent
    one — is never deleted from under it.
    """
    current = await ENVIRONMENTS.get(slug)
    if current is not None and current.snapshot_id == snapshot_id:
        return
    await _delete_snapshot(snapshot_id)


async def retire_superseded_snapshot(slug: str, previous_id: str | None, current_id: str) -> None:
    """Delete the snapshot ``current_id`` replaced, if the record still points at ours.

    Re-reads first: a concurrent capture may have pointed the record at its own
    snapshot, and deleting ours then would strand it.
    """
    if not previous_id or previous_id == current_id:
        return
    current = await ENVIRONMENTS.get(slug)
    if current is not None and current.snapshot_id == current_id:
        await _delete_snapshot(previous_id)
