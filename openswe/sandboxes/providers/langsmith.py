"""LangSmith sandbox backend integration."""

import asyncio
import base64
import json
import logging
import shlex
from abc import ABC, abstractmethod
from typing import Any, Literal, NotRequired, TypedDict

import httpx2
from deepagents.backends import LangSmithSandbox
from deepagents.backends.protocol import (
    DeleteResult,
    ExecuteResponse,
    FileInfo,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)
from langsmith.sandbox import (
    AsyncSandboxClient,
    CommandTimeoutError,
    ResourceNotFoundError,
    SandboxClientError,
    SandboxConnectionError,
    SandboxOperationError,
    SandboxServerReloadError,
)
from pydantic import BaseModel, JsonValue, TypeAdapter

from openswe.config import ENV
from openswe.sandboxes.paths import WORKSPACE_DIR
from openswe.sandboxes.providers.langsmith_api import SandboxAPI
from openswe.sandboxes.providers.registry import SandboxGoneError
from openswe.sandboxes.retry import retry_transient_sandbox_errors
from openswe.utils.http_pool import pooled_transport
from openswe.utils.startup_trace import asubphase
from openswe.workspaces.cloud_identity import federated_cloud

logger = logging.getLogger(__name__)

try:
    from langsmith.sandbox import SandboxNotReadyError
except ImportError:  # pragma: no cover - depends on langsmith SDK version
    SANDBOX_NOT_READY_ERRORS: tuple[type[BaseException], ...] = ()
else:
    SANDBOX_NOT_READY_ERRORS = (SandboxNotReadyError,)

DEFAULT_SNAPSHOT_FS_CAPACITY_BYTES = 128 * 1024**3
DEFAULT_SANDBOX_VCPUS = 4
DEFAULT_SANDBOX_MEM_BYTES = 16 * 1024**3
DEFAULT_SANDBOX_IDLE_TTL_SECONDS = 2 * 60 * 60  # 2 hours
DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS = 30 * 24 * 60 * 60  # 30 days
SANDBOX_CREATE_MAX_ATTEMPTS = 3
SANDBOX_CREATE_RETRY_DELAYS_SECONDS = (1.0, 3.0)
SANDBOX_CREATE_RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
PROXY_CONFIG_TIMEOUT_SECONDS = 10.0
# The API cannot push a proxy config while the host suspends an idle box, which
# takes up to about three minutes; outlast that rather than fail the run.
PROXY_CONFIG_RETRY_WINDOW_SECONDS = 180.0
PROXY_CONFIG_RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
PROXY_GH_TOKEN_PLACEHOLDER = "proxy-injected"
SERVICE_URL_TIMEOUT_SECONDS = 15.0
_KILL_TIMEOUT_SECONDS = 5.0
# The most entries the sandbox file API returns for one listing.
_LS_MAX_ENTRIES = 1000
# Above this, a file API read would pull the whole file into the worker to return
# one page; the base class's read script pages inside the sandbox instead.
_FILE_API_READ_MAX_BYTES = 2 * 1024 * 1024


def service_identity_jwks_url() -> str:
    """Where an app in a sandbox verifies the identity token LangSmith forwards to it.

    The keys are served by the API host, not by the app origin the token names as
    its issuer.
    """
    return f"{SandboxAPI.from_env().api_root}/.well-known/jwks.json"


def _parse_optional_int(name: str, default: int) -> int:
    raw = ENV[name].optional()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as e:
        msg = f"{name} must be an integer, got {raw!r}"
        raise ValueError(msg) from e


def _execute_client_grace_seconds() -> int:
    """Extra wall-clock seconds the client waits past a command's own timeout
    before giving up and killing it. The server is meant to enforce the command
    timeout; this is the client-side backstop for when it doesn't."""
    return _parse_optional_int("SANDBOX_EXECUTE_CLIENT_GRACE_SECONDS", 30)


def _get_sandbox_snapshot_config() -> tuple[int, int, int, int, int]:
    """Get sandbox snapshot configuration from environment."""
    fs_capacity_bytes = _parse_optional_int(
        "DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES", DEFAULT_SNAPSHOT_FS_CAPACITY_BYTES
    )
    vcpus = _parse_optional_int("DEFAULT_SANDBOX_VCPUS", DEFAULT_SANDBOX_VCPUS)
    mem_bytes = _parse_optional_int("DEFAULT_SANDBOX_MEM_BYTES", DEFAULT_SANDBOX_MEM_BYTES)
    idle_ttl_seconds = _parse_optional_int(
        "DEFAULT_SANDBOX_IDLE_TTL_SECONDS", DEFAULT_SANDBOX_IDLE_TTL_SECONDS
    )
    delete_after_stop_seconds = _parse_optional_int(
        "DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS",
        DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS,
    )
    return (
        fs_capacity_bytes,
        vcpus,
        mem_bytes,
        idle_ttl_seconds,
        delete_after_stop_seconds,
    )


def _get_sandbox_create_extra_fields() -> dict[str, Any]:
    """Parse SANDBOX_CREATE_EXTRA_JSON into extra fields merged into the
    sandbox-create request body, e.g. ``{"_internal_runtime": "v2"}``."""
    raw = ENV.SANDBOX_CREATE_EXTRA_JSON.optional()
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        msg = f"SANDBOX_CREATE_EXTRA_JSON must be valid JSON, got {raw!r}"
        raise ValueError(msg) from e
    if not isinstance(parsed, dict):
        msg = f"SANDBOX_CREATE_EXTRA_JSON must be a JSON object, got {type(parsed).__name__}"
        raise ValueError(msg)
    return parsed


def _merge_sandbox_create_extra_fields(
    create_params: dict[str, Any] | None,
) -> dict[str, Any]:
    return {**_get_sandbox_create_extra_fields(), **(create_params or {})}


def get_sandbox_proxy_config(
    create_params: dict[str, Any] | None,
) -> dict[str, Any] | None:
    proxy_config = _merge_sandbox_create_extra_fields(create_params).get("proxy_config")
    return dict(proxy_config) if isinstance(proxy_config, dict) else None


_json_object = TypeAdapter(dict[str, JsonValue])


class LangSmithCreateOptions(BaseModel):
    """What a new sandbox is created with, the deployment's defaults filled in."""

    snapshot_id: str | None = None
    fs_capacity_bytes: int
    vcpus: int | None = None
    mem_bytes: int | None = None
    idle_ttl_seconds: int
    delete_after_stop_seconds: int
    # Create-request fields the SDK does not model, such as ``preserve_memory_on_stop``.
    extra_fields: dict[str, JsonValue] = {}

    @classmethod
    def resolve(
        cls,
        *,
        snapshot_id: str | None = None,
        mem_bytes: int | None = None,
        vcpus: int | None = None,
        fs_capacity_bytes: int | None = None,
        create_params: dict[str, Any] | None = None,
    ) -> LangSmithCreateOptions:
        (
            default_fs_capacity_bytes,
            default_vcpus,
            default_mem_bytes,
            idle_ttl_seconds,
            delete_after_stop_seconds,
        ) = _get_sandbox_snapshot_config()
        if mem_bytes is None and vcpus is None:
            mem_bytes, vcpus = default_mem_bytes, default_vcpus
        extra_fields = _merge_sandbox_create_extra_fields(create_params)
        # The API boots its default root snapshot only when the key is absent:
        # `snapshot_id` is a UUID server-side, so "" is rejected with a 422.
        if not extra_fields.get("snapshot_id"):
            extra_fields.pop("snapshot_id", None)
        return cls(
            snapshot_id=snapshot_id or None,
            fs_capacity_bytes=(
                fs_capacity_bytes if fs_capacity_bytes is not None else default_fs_capacity_bytes
            ),
            vcpus=vcpus,
            mem_bytes=mem_bytes,
            idle_ttl_seconds=idle_ttl_seconds,
            delete_after_stop_seconds=delete_after_stop_seconds,
            extra_fields=_json_object.validate_python(extra_fields),
        )


def _install_create_extra_fields(client: AsyncSandboxClient, extra: dict[str, Any]) -> None:
    """Merge extra fields until the SDK exposes create-payload passthrough."""
    if not extra:
        return
    original_post = client._http.post  # noqa: SLF001

    async def post_with_extra(url: Any, *args: Any, **kwargs: Any) -> Any:
        payload = kwargs.get("json")
        if str(url).endswith("/boxes") and isinstance(payload, dict):
            kwargs["json"] = {**payload, **extra}
        return await original_post(url, *args, **kwargs)

    # RFC moving this into the SDK if it adds a public arbitrary create-fields API.
    client._http.post = post_with_extra  # noqa: SLF001 # ty: ignore[invalid-assignment]


class GitHubProxyHeader(TypedDict):
    name: str
    type: Literal["opaque", "plaintext"]
    value: str


class GitHubProxyRule(TypedDict):
    name: str
    match_hosts: list[str]
    headers: list[GitHubProxyHeader]
    env_vars: NotRequired[dict[str, str]]


def _github_proxy_rules(github_token: str | None) -> list[GitHubProxyRule]:
    if not github_token:
        return []
    basic_auth = base64.b64encode(f"x-access-token:{github_token}".encode()).decode()
    # GitHub enforces repository IDs on the token, including for mixed-case URLs.
    return [
        {
            "name": "github-api",
            "match_hosts": ["api.github.com"],
            "headers": [
                {
                    "name": "Authorization",
                    "type": "opaque",
                    "value": f"Bearer {github_token}",
                }
            ],
            # `gh` refuses to run without a token in its environment even though the
            # proxy injects the real one on the wire.
            "env_vars": {"GH_TOKEN": PROXY_GH_TOKEN_PLACEHOLDER},
        },
        {
            "name": "github",
            "match_hosts": ["github.com", "*.github.com"],
            "headers": [
                {
                    "name": "Authorization",
                    "type": "opaque",
                    "value": f"Basic {basic_auth}",
                }
            ],
        },
    ]


def _proxy_config_retry_after(exc: BaseException) -> float | None:
    if not isinstance(exc, httpx2.HTTPStatusError):
        return None
    raw = exc.response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        delay = float(raw)
    except ValueError:
        return None
    return max(delay, 0.0)


def _is_retryable_proxy_config_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx2.HTTPStatusError):
        return exc.response.status_code in PROXY_CONFIG_RETRYABLE_STATUS_CODES
    return isinstance(exc, httpx2.TransportError)


def _is_retryable_sandbox_create_error(exc: BaseException) -> bool:
    # A stalled create may have succeeded server-side; an orphan its idle TTL stops beats failing.
    if isinstance(exc, httpx2.TransportError):
        return True
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code in SANDBOX_CREATE_RETRYABLE_STATUS_CODES
    return exc.__class__.__name__ in {
        "ResourceCreationError",
        "SandboxAPIError",
        "SandboxConnectionError",
        "SandboxNotReadyError",
    }


async def _reuse_existing_sandbox(client: AsyncSandboxClient, sandbox_id: str) -> Any:
    try:
        return await client.get_sandbox(name=sandbox_id)
    except ResourceNotFoundError as e:
        msg = f"Failed to connect to existing sandbox '{sandbox_id}': {e}"
        raise SandboxGoneError(msg) from e
    except Exception as e:
        msg = f"Failed to connect to existing sandbox '{sandbox_id}': {e}"
        raise RuntimeError(msg) from e


async def _create_sandbox_with_retry(
    client: AsyncSandboxClient,
    *,
    snapshot_id: str,
    fs_capacity_bytes: int | None,
    vcpus: int | None,
    mem_bytes: int | None,
    idle_ttl_seconds: int | None,
    delete_after_stop_seconds: int | None,
    timeout: int,
) -> Any:
    for attempt in range(SANDBOX_CREATE_MAX_ATTEMPTS):
        try:
            return await client.create_sandbox(
                snapshot_id=snapshot_id,
                fs_capacity_bytes=fs_capacity_bytes,
                vcpus=vcpus,
                mem_bytes=mem_bytes,
                idle_ttl_seconds=idle_ttl_seconds,
                delete_after_stop_seconds=delete_after_stop_seconds,
                timeout=timeout,
            )
        except Exception as exc:
            if attempt == SANDBOX_CREATE_MAX_ATTEMPTS - 1 or not _is_retryable_sandbox_create_error(
                exc
            ):
                raise
            delay = SANDBOX_CREATE_RETRY_DELAYS_SECONDS[
                min(attempt, len(SANDBOX_CREATE_RETRY_DELAYS_SECONDS) - 1)
            ]
            logger.warning(
                "Failed to create LangSmith sandbox; retrying",
                extra={"error_type": type(exc).__name__, "attempt": attempt + 1, "delay_s": delay},
            )
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable sandbox retry state")


async def _patch_proxy_config(
    client: httpx2.AsyncClient,
    url: str,
    payload: dict[str, Any],
    api: SandboxAPI,
    sandbox_name: str,
) -> None:
    async def patch() -> None:
        response = await client.patch(url, json=payload, headers=api.headers())
        api.raise_for_status(response)

    await retry_transient_sandbox_errors(
        patch,
        description=f"GitHub proxy config for sandbox {sandbox_name}",
        max_elapsed=PROXY_CONFIG_RETRY_WINDOW_SECONDS,
        retryable=_is_retryable_proxy_config_error,
        retry_after=_proxy_config_retry_after,
    )


async def _federated_proxy_rules(
    client: httpx2.AsyncClient, url: str, api: SandboxAPI, sandbox_name: str
) -> list[dict[str, JsonValue]]:
    async def get() -> dict[str, JsonValue]:
        response = await client.get(url, headers=api.headers())
        api.raise_for_status(response)
        return _json_object.validate_python(response.json())

    sandbox = await retry_transient_sandbox_errors(
        get,
        description=f"proxy config of sandbox {sandbox_name}",
        max_elapsed=PROXY_CONFIG_RETRY_WINDOW_SECONDS,
        retryable=_is_retryable_proxy_config_error,
        retry_after=_proxy_config_retry_after,
    )
    current = sandbox.get("proxy_config")
    rules = current.get("rules") if isinstance(current, dict) else None
    return [
        rule
        for rule in (rules if isinstance(rules, list) else [])
        if isinstance(rule, dict) and federated_cloud(rule)
    ]


async def configure_sandbox_proxy(
    sandbox_name: str,
    github_token: str | None,
    *,
    base_proxy_config: dict[str, Any] | None = None,
    thread_id: str | None = None,
) -> None:
    """Inject GitHub and thread-tool credentials while preserving custom proxy settings."""
    api = SandboxAPI.from_env()
    if not api.api_key:
        logger.warning("No LangSmith API key found, skipping sandbox proxy configuration")
        return
    url = f"{api.endpoint}/boxes/{sandbox_name}"
    proxy_config = dict(base_proxy_config or {})
    custom_rules = proxy_config.get("rules")
    # Retire credentials saved by removed built-in integrations.
    preserved_rules = [
        rule
        for rule in (custom_rules if isinstance(custom_rules, list) else [])
        if not isinstance(rule, dict)
        or rule.get("name")
        not in {"github", "github-api", "github-public", "open-swe-langsmith", "stagehand-model"}
    ]
    from openswe.sandboxes.tool_access import TOOLS_RULE, tool_proxy_rule

    preserved_rules = [
        rule
        for rule in preserved_rules
        if not isinstance(rule, dict) or rule.get("name") != TOOLS_RULE
    ]
    tools_rule = await tool_proxy_rule(thread_id, sandbox_name) if thread_id else None
    async with httpx2.AsyncClient(
        transport=pooled_transport(), timeout=PROXY_CONFIG_TIMEOUT_SECONDS
    ) as client:
        # Federation is fixed at creation, including its absence on older boxes, and
        # LangSmith allows one rule per cloud.
        federated_rules = await _federated_proxy_rules(client, url, api, sandbox_name)
        federated_clouds = {rule.get("type") for rule in federated_rules}
        proxy_config["rules"] = [
            *_github_proxy_rules(github_token),
            *([tools_rule] if tools_rule else []),
            *(
                rule
                for rule in preserved_rules
                if not federated_cloud(rule)
                and not (isinstance(rule, dict) and rule.get("type") in federated_clouds)
            ),
            *federated_rules,
        ]
        await _patch_proxy_config(client, url, {"proxy_config": proxy_config}, api, sandbox_name)
    logger.info("Configured sandbox proxy", extra={"sandbox_id": sandbox_name})


class WorkspaceServiceURL(BaseModel):
    """LangSmith's reply to sharing a sandbox port with the workspace."""

    service_url: str
    access: Literal["workspace"]


async def create_workspace_service_url(sandbox_id: str, port: int) -> str:
    """Share ``port`` of a sandbox with the workspace and return its URL.

    The URL carries no token and never expires, so the same link keeps working
    for as long as the sandbox does; its viewers authenticate with their own
    LangSmith session. LangSmith refuses it while an unexpired service token
    exists for the same port, and while the tenant's
    ``sandbox_service_url_langsmith_login`` flag is off.
    """
    response = await SandboxAPI.from_env().request(
        "POST",
        f"/boxes/{sandbox_id}/service-url",
        json={"port": port, "access": "workspace"},
        timeout=SERVICE_URL_TIMEOUT_SECONDS,
    )
    return WorkspaceServiceURL.model_validate(response.json()).service_url


def get_async_sandbox_client() -> AsyncSandboxClient:
    """Build an ``AsyncSandboxClient`` from the resolved sandbox LangSmith credentials."""
    api = SandboxAPI.from_env()
    return AsyncSandboxClient(api_key=api.api_key, api_endpoint=api.endpoint)


async def capture_snapshot_with_tag(
    client: AsyncSandboxClient,
    sandbox_id: str,
    name: str,
    tag: str,
    *,
    timeout: int,
) -> Any:
    """Capture ``sandbox_id`` as ``name:tag``.

    Snapshots are Docker-style: ``name:tag`` is a mutable pointer at immutable
    content, so re-capturing a tag moves it rather than colliding. The Python SDK
    has no ``tag`` parameter yet, so the field is injected into the capture body
    the same way ``_install_create_extra_fields`` injects sandbox-create fields.
    Drop this for a plain ``capture_snapshot(..., tag=...)`` once
    langchain-ai/langsmith-sdk#3447 ships.
    """
    # Reaching into the SDK's transport is the whole mechanism: there is no public
    # seam for a field the client does not model.
    original_post = client._http.post  # noqa: SLF001

    async def post_with_tag(url: Any, *args: Any, **kwargs: Any) -> Any:
        payload = kwargs.get("json")
        if str(url).endswith("/snapshot") and isinstance(payload, dict):
            kwargs["json"] = {**payload, "tag": tag}
        return await original_post(url, *args, **kwargs)

    client._http.post = post_with_tag  # noqa: SLF001 # ty: ignore[invalid-assignment]
    try:
        return await client.capture_snapshot(sandbox_id, name, timeout=timeout)
    finally:
        client._http.post = original_post  # noqa: SLF001 # ty: ignore[invalid-assignment]


async def sandbox_exists(sandbox_id: str) -> bool:
    """Whether LangSmith still has the sandbox; raises when it cannot tell."""
    async with get_async_sandbox_client() as client:
        try:
            await client.get_sandbox(name=sandbox_id)
        except ResourceNotFoundError:
            return False
    return True


async def connect_async_langsmith_sandbox(sandbox_id: str) -> tuple[AsyncSandboxClient, Any]:
    client = get_async_sandbox_client()
    try:
        return client, await client.get_sandbox(name=sandbox_id)
    except Exception:
        await client.aclose()
        raise


async def create_langsmith_sandbox(
    sandbox_id: str | None = None,
    github_token: str | None = None,
    *,
    snapshot_id: str | None = None,
    mem_bytes: int | None = None,
    vcpus: int | None = None,
    fs_capacity_bytes: int | None = None,
    create_params: dict[str, Any] | None = None,
    prepare_work_dir: bool = True,
) -> SandboxBackendProtocol:
    """Create or connect to a LangSmith sandbox without automatic cleanup.

    This function directly uses the LangSmithProvider to create/connect to sandboxes
    without the context manager cleanup, allowing sandboxes to persist across
    multiple agent invocations.

    Args:
        sandbox_id: Optional existing sandbox ID to connect to.
                   If None, creates a new sandbox.
        github_token: Optional GitHub token. Used to configure proxy auth on
                      new sandboxes. Ignored when connecting to an existing sandbox.
        snapshot_id: Optional repo-scoped snapshot to boot from. When omitted,
            uses the API's root snapshot.
        mem_bytes: Optional memory capacity override for a newly-created sandbox.
        vcpus: Optional virtual CPU count override for a newly-created sandbox.
        fs_capacity_bytes: Optional filesystem capacity override for a newly-created sandbox.
        create_params: Optional additional fields merged into the sandbox create body.
        prepare_work_dir: Whether a new sandbox creates ``/workspace`` before returning.

    Returns:
        SandboxBackendProtocol instance
    """
    options = LangSmithCreateOptions.resolve(
        snapshot_id=snapshot_id,
        mem_bytes=mem_bytes,
        vcpus=vcpus,
        fs_capacity_bytes=fs_capacity_bytes,
        create_params=create_params,
    )
    provider = LangSmithProvider(api_key=SandboxAPI.from_env().api_key)
    backend = await provider.get_or_create(
        sandbox_id=sandbox_id,
        snapshot_id=options.snapshot_id or "",
        fs_capacity_bytes=options.fs_capacity_bytes,
        vcpus=options.vcpus,
        mem_bytes=options.mem_bytes,
        idle_ttl_seconds=options.idle_ttl_seconds,
        delete_after_stop_seconds=options.delete_after_stop_seconds,
        create_params=create_params,
    )

    if sandbox_id is None and prepare_work_dir:
        await backend.aexecute(f"mkdir -p {WORKSPACE_DIR}")
    if sandbox_id is None and github_token:
        proxy_config = get_sandbox_proxy_config(create_params)
        if proxy_config is not None:
            await configure_sandbox_proxy(
                backend.id,
                github_token,
                base_proxy_config=proxy_config,
            )
        else:
            await configure_sandbox_proxy(backend.id, github_token)

    return backend


def _log_abandoned_result(task: asyncio.Future[Any]) -> None:
    if not task.cancelled() and (exc := task.exception()) is not None:
        logger.debug("Killed sandbox command ended with an error", exc_info=exc)


class TimeoutLangSmithSandbox(LangSmithSandbox):
    """LangSmith backend that enforces a client-side execution deadline.

    The langsmith SDK's default execute path is now a WebSocket stream with no
    client-side read deadline: on a live socket where the dataplane never emits
    an exit/error frame, ``CommandHandle.result`` blocks forever and wedges the
    run (the blocking call sits in a thread that cancellation can't reclaim).

    We drive a non-blocking ``CommandHandle`` ourselves and, if the command
    overruns its own timeout by the grace window, kill it and surface a
    timed-out tool result instead of hanging the graph. WebSocket connect
    failures fall back to the base wait=True path, whose HTTP fallback carries
    its own request deadline.
    """

    @property
    def sandbox(self) -> Any:
        return self._sandbox

    def get_work_dir(self) -> str:
        """Repositories live in ``/workspace/<repo>``, where workspace images preload them."""
        return WORKSPACE_DIR

    _WS_FALLBACK_ERRORS = (
        SandboxConnectionError,
        SandboxServerReloadError,
        ImportError,
        OSError,
        TypeError,
    )

    def _deadline(self, effective_timeout: int) -> int:
        return effective_timeout + _execute_client_grace_seconds()

    @staticmethod
    def _result_to_response(result: Any) -> ExecuteResponse:
        output = result.stdout or ""
        if result.stderr:
            output += "\n" + result.stderr if output else result.stderr
        return ExecuteResponse(output=output, exit_code=result.exit_code, truncated=False)

    @staticmethod
    def _timeout_response(seconds: int, *, server_side: bool) -> ExecuteResponse:
        where = "on the sandbox" if server_side else "by the client and killed"
        return ExecuteResponse(
            output=f"Command timed out after {seconds}s {where}.",
            exit_code=124,
            truncated=False,
        )

    @staticmethod
    async def _asafe_kill(handle: Any) -> None:
        try:
            await handle.kill()
        except Exception:  # noqa: BLE001 - best-effort cleanup of a wedged command
            logger.warning("Failed to kill timed-out sandbox command", exc_info=True)

    async def _akill_and_drain(self, handle: Any, result_task: asyncio.Future[Any]) -> None:
        """Kill the command, then release its stream, each within a bounded wait."""
        kill = asyncio.ensure_future(self._asafe_kill(handle))
        await asyncio.wait({kill}, timeout=_KILL_TIMEOUT_SECONDS)
        kill.cancel()
        result_task.add_done_callback(_log_abandoned_result)
        result_task.cancel()
        await asyncio.wait({result_task}, timeout=_KILL_TIMEOUT_SECONDS)

    async def _abase_execute(self, command: str, timeout: int | None) -> ExecuteResponse:
        return await LangSmithSandbox.aexecute(self, command, timeout=timeout)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError("TimeoutLangSmithSandbox is async-only; use aexecute.")

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """Read small files through the sandbox file API rather than a read script.

        Every command opens its own exec WebSocket, while the file API reuses the
        client's pooled HTTP connection. The file API returns the whole file, so
        large files, and files the API cannot stat, go through the read script,
        which pages inside the sandbox.
        """
        try:
            stat = await self._aget_sandbox().stat(file_path)
        except SandboxClientError:
            logger.debug("Sandbox file API stat failed; using the read script", exc_info=True)
            return await super().aread(file_path, offset, limit)
        if stat.size_bytes > _FILE_API_READ_MAX_BYTES:
            return await super().aread(file_path, offset, limit)
        return await asyncio.to_thread(self.read, file_path, offset, limit)

    async def als(self, path: str) -> LsResult:
        """List through the sandbox file API, keeping the listing command for its edge cases.

        The file API caps a listing and answers a file path with no entries, so a
        capped, empty or failed listing is redone by the base class's command.
        """
        try:
            listing = await self._aget_sandbox().ls(path, limit=_LS_MAX_ENTRIES)
        except ResourceNotFoundError:
            return LsResult(error=f"Path '{path}': path_not_found")
        except SandboxClientError:
            logger.debug(
                "Sandbox file API listing failed; using the listing command", exc_info=True
            )
            return await super().als(path)
        if listing.truncated or not listing.matches:
            return await super().als(path)
        return LsResult(entries=[FileInfo(path=m.path, is_dir=m.is_dir) for m in listing.matches])

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """Upload through the sandbox file API, which creates missing parent directories.

        The base class first runs a ``mkdir -p`` preflight as its own command.
        """
        try:
            await self._aget_sandbox().write(file_path, content.encode("utf-8"))
        except SandboxClientError as e:
            return WriteResult(error=f"Failed to write file '{file_path}': {e}")
        return WriteResult(path=file_path)

    async def adelete(self, file_path: str) -> DeleteResult:
        quoted = shlex.quote(file_path)
        exists = await self.aexecute(f"test -e {quoted} || test -L {quoted}")
        if exists.exit_code is not None and exists.exit_code != 0:
            return DeleteResult(error=f"Error: '{file_path}' not found")
        result = await self.aexecute(f"rm -rf {quoted}")
        if result.exit_code == 0:
            return DeleteResult(path=file_path)
        return DeleteResult(
            error=f"Error deleting file '{file_path}': {result.output.strip() or 'unknown error'}"
        )

    async def aexecute(
        self,
        command: str,
        *,
        timeout: int | None = None,  # noqa: ASYNC109 - forwarded semantic timeout, not an asyncio contract
    ) -> ExecuteResponse:
        return await retry_transient_sandbox_errors(
            lambda: self._aexecute_once(command, timeout=timeout),
            description="Sandbox command",
        )

    async def _aexecute_once(
        self,
        command: str,
        *,
        timeout: int | None = None,  # noqa: ASYNC109 - forwarded semantic timeout, not an asyncio contract
    ) -> ExecuteResponse:
        effective = timeout if timeout is not None else self._default_timeout
        if not effective:
            return await super().aexecute(command, timeout=timeout)
        # run(wait=False) opens the WS and reads the "started" frame, so
        # connect/setup failures raise here — fall back to the base path.
        try:
            async with asubphase("sandbox.exec.connect", sandbox_id=self.id):
                handle = await self._aget_sandbox().run(command, timeout=effective, wait=False)
        except (*self._WS_FALLBACK_ERRORS, *SANDBOX_NOT_READY_ERRORS, TimeoutError) as exc:
            return await self._afallback_execute(command, timeout, exc)
        deadline = self._deadline(effective)
        # The kill frame travels over the command's own stream, so the kill has
        # to go out before that stream is torn down: waiting on a separate task
        # keeps it open through a timeout or a cancelled run.
        result_task = asyncio.ensure_future(handle.result)
        try:
            async with asubphase("sandbox.exec.result", sandbox_id=self.id):
                await asyncio.wait({result_task}, timeout=deadline)
        except asyncio.CancelledError:
            await self._akill_and_drain(handle, result_task)
            raise
        if not result_task.done():
            await self._akill_and_drain(handle, result_task)
            return self._timeout_response(deadline, server_side=False)
        try:
            result = result_task.result()
        except CommandTimeoutError:
            return self._timeout_response(effective, server_side=True)
        except SandboxOperationError as exc:
            if exc.error_type != "CommandNotFound" or exc.operation != "reconnect":
                raise
            logger.exception(
                "Sandbox lost command state while collecting its result",
                extra={"sandbox_id": self.id, "sandbox_error_type": exc.error_type},
            )
            msg = (
                f"Sandbox {self.id} lost command state while collecting the result: {exc}. "
                "The command may already have run; its outcome is unknown. "
                "This is not a missing shell executable. No automatic retry was attempted. "
                "Check the command's effects before deciding whether to run it again."
            )
            raise RuntimeError(msg) from exc
        except (*self._WS_FALLBACK_ERRORS, *SANDBOX_NOT_READY_ERRORS) as exc:
            return await self._afallback_execute(command, timeout, exc)
        return self._result_to_response(result)

    async def _afallback_execute(
        self, command: str, timeout: int | None, cause: BaseException
    ) -> ExecuteResponse:
        async with asubphase(
            "sandbox.exec.http_fallback", sandbox_id=self.id, reason=type(cause).__name__
        ):
            return await self._abase_execute(command, timeout)


class SandboxProvider(ABC):
    """Interface for creating sandbox backends.

    Intentionally has no delete. A sandbox holds the agent's only copy of its
    working tree, and the thread metadata read fails open to "no sandbox", so a
    delete keyed off it can destroy a live box. Reclamation is the platform's
    job, via the idle TTL and delete-after-stop set at create time.
    """

    @abstractmethod
    async def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        **kwargs: Any,
    ) -> SandboxBackendProtocol:
        """Get an existing sandbox, or create one if needed."""
        raise NotImplementedError


class LangSmithProvider(SandboxProvider):
    """LangSmith sandbox provider implementation."""

    def __init__(self, api_key: str | None = None) -> None:
        api = SandboxAPI.from_env()
        self._api_key = api_key or api.api_key
        self._api_endpoint = api.endpoint
        if not self._api_key:
            msg = "LANGSMITH_API_KEY not set"
            raise ValueError(msg)

    @classmethod
    def validate_startup_config(cls) -> None:
        """Validate env-var configuration at server startup. Raises ValueError if invalid."""
        for name in (
            "DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES",
            "DEFAULT_SANDBOX_VCPUS",
            "DEFAULT_SANDBOX_MEM_BYTES",
            "DEFAULT_SANDBOX_IDLE_TTL_SECONDS",
            "DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS",
        ):
            raw = ENV[name].optional()
            if raw is None:
                continue
            try:
                value = int(raw)
            except ValueError as e:
                msg = f"{name} must be an integer, got {raw!r}"
                raise ValueError(msg) from e
            if (
                name
                in {
                    "DEFAULT_SANDBOX_IDLE_TTL_SECONDS",
                    "DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS",
                }
                and value < 0
            ):
                msg = f"{name} must be >= 0, got {value}"
                raise ValueError(msg)
        _get_sandbox_create_extra_fields()

    async def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        timeout: int = 180,
        snapshot_id: str | None = None,
        fs_capacity_bytes: int | None = None,
        vcpus: int | None = None,
        mem_bytes: int | None = None,
        idle_ttl_seconds: int | None = None,
        delete_after_stop_seconds: int | None = None,
        create_params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> SandboxBackendProtocol:
        """Get existing or create new LangSmith sandbox.

        Provisioning runs natively async via ``AsyncSandboxClient``. The
        resulting ``AsyncSandbox`` is converted to a sync ``Sandbox`` via
        ``to_sync()`` so it satisfies the deepagents sync ``SandboxBackendProtocol``
        that ``TimeoutLangSmithSandbox`` and the agent's file/execute tools expect.
        """
        if kwargs:
            msg = f"Received unsupported arguments: {list(kwargs.keys())}"
            raise TypeError(msg)
        async with AsyncSandboxClient(
            api_key=self._api_key, api_endpoint=self._api_endpoint
        ) as client:
            if sandbox_id:
                sandbox = await _reuse_existing_sandbox(client, sandbox_id)
                return TimeoutLangSmithSandbox(sandbox.to_sync())

            effective_snapshot_id = snapshot_id or ""
            extra_fields = _merge_sandbox_create_extra_fields(create_params)
            # The API boots its default root snapshot only when the key is absent:
            # `snapshot_id` is a UUID server-side, so "" is rejected with a 422.
            if not extra_fields.get("snapshot_id"):
                extra_fields.pop("snapshot_id", None)
            _install_create_extra_fields(client, extra_fields)

            try:
                sandbox = await _create_sandbox_with_retry(
                    client,
                    snapshot_id=effective_snapshot_id,
                    fs_capacity_bytes=fs_capacity_bytes,
                    vcpus=vcpus,
                    mem_bytes=mem_bytes,
                    idle_ttl_seconds=idle_ttl_seconds,
                    delete_after_stop_seconds=delete_after_stop_seconds,
                    timeout=timeout,
                )
            except Exception as e:
                msg = f"Failed to create sandbox from snapshot '{snapshot_id}': {e}"
                raise RuntimeError(msg) from e

            return TimeoutLangSmithSandbox(sandbox.to_sync())
