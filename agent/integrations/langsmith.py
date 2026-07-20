"""LangSmith sandbox backend integration."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any

import httpx
from deepagents.backends import LangSmithSandbox
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol
from langsmith.sandbox import (
    AsyncSandboxClient,
    CommandTimeoutError,
    SandboxConnectionError,
    SandboxServerReloadError,
)

logger = logging.getLogger(__name__)

try:
    from langsmith.sandbox import SandboxNotReadyError
except ImportError:  # pragma: no cover - depends on langsmith SDK version
    SANDBOX_NOT_READY_ERRORS: tuple[type[BaseException], ...] = ()
else:
    SANDBOX_NOT_READY_ERRORS = (SandboxNotReadyError,)

DEFAULT_SNAPSHOT_FS_CAPACITY_BYTES = 32 * 1024**3
DEFAULT_SANDBOX_VCPUS = 2
DEFAULT_SANDBOX_MEM_BYTES = 7936 * 1024**2  # 7936 MiB ("large" tier cap)
DEFAULT_SANDBOX_IDLE_TTL_SECONDS = 2 * 60 * 60  # 2 hours
DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS = 24 * 60 * 60  # 24 hours
SANDBOX_CREATE_MAX_ATTEMPTS = 3
SANDBOX_CREATE_RETRY_DELAYS_SECONDS = (1.0, 3.0)
SANDBOX_CREATE_RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
SANDBOX_READY_STATUSES = frozenset({"ready", "running"})
SANDBOX_RECONNECT_STARTABLE_STATUSES = frozenset({"stopped", "paused", "idle"})
SANDBOX_RECONNECT_PENDING_STATUSES = frozenset({"creating", "pending", "starting", "resuming"})
SANDBOX_RECONNECT_READY_TIMEOUT_SECONDS = 30.0
SANDBOX_RECONNECT_READY_POLL_SECONDS = 2.0
PROXY_CONFIG_MAX_ATTEMPTS = 3
PROXY_CONFIG_TIMEOUT_SECONDS = 10.0
PROXY_CONFIG_RETRY_DELAYS_SECONDS = (0.5, 1.0)
PROXY_CONFIG_RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


def _get_langsmith_api_key() -> str | None:
    """Get LangSmith API key from environment.

    Checks LANGSMITH_API_KEY first, then falls back to LANGSMITH_API_KEY_PROD
    for LangGraph Cloud deployments where LANGSMITH_API_KEY is reserved.
    """
    return os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGSMITH_API_KEY_PROD")


def _get_sandbox_api_key() -> str | None:
    """LangSmith API key for sandbox operations.

    ``SANDBOX_LANGSMITH_API_KEY`` lets sandboxes run against a different
    LangSmith workspace than the one used for tracing/other API calls; falls
    back to the standard key.
    """
    return os.environ.get("SANDBOX_LANGSMITH_API_KEY") or _get_langsmith_api_key()


def _get_sandbox_endpoint() -> str:
    """LangSmith API **root** for sandbox operations.

    Overridable via ``SANDBOX_LANGSMITH_ENDPOINT`` to pair with
    ``SANDBOX_LANGSMITH_API_KEY``; falls back to ``LANGSMITH_ENDPOINT``. This is
    the bare root (e.g. ``https://api.smith.langchain.com``) used to build the
    proxy-config URL; the SDK clients take :func:`_get_sandbox_api_endpoint`.
    """
    return (
        os.environ.get("SANDBOX_LANGSMITH_ENDPOINT")
        or os.environ.get("LANGSMITH_ENDPOINT")
        or "https://api.smith.langchain.com"
    )


def _get_sandbox_api_endpoint() -> str:
    """Sandbox API base URL for the langsmith SDK clients.

    The SDK's ``api_endpoint`` is the sandbox base (root + ``/v2/sandboxes``),
    not the API root, and its methods append ``/boxes``, ``/snapshots``, etc.
    """
    root = _get_sandbox_endpoint().rstrip("/")
    suffix = "/v2/sandboxes"
    return root if root.endswith(suffix) else f"{root}{suffix}"


def _current_thread_id() -> str | None:
    """The LangGraph thread id for the active run, if any."""
    try:
        from langgraph.config import get_config

        return get_config().get("configurable", {}).get("thread_id")
    except Exception:
        return None


def _sandbox_name_for_thread(thread_id: str | None) -> str | None:
    """Deterministic, thread-traceable sandbox name: ``openswe-<b32(thread uuid)>``.

    The thread id (a UUID) is base32-encoded lowercase without padding so the
    name is a compact, hyphen-free token that maps back to the thread. Returns
    None when the thread id is missing or not a UUID, leaving the name unset.
    """
    if not thread_id:
        return None
    try:
        raw = uuid.UUID(thread_id).bytes
    except ValueError:
        return None
    encoded = base64.b32encode(raw).decode("ascii").rstrip("=").lower()
    return f"openswe-{encoded}"


def _parse_optional_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
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


def _get_sandbox_snapshot_config() -> tuple[str | None, int, int, int, int, int]:
    """Get sandbox snapshot configuration from environment."""
    snapshot_id = os.environ.get("DEFAULT_SANDBOX_SNAPSHOT_ID")
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
        snapshot_id,
        fs_capacity_bytes,
        vcpus,
        mem_bytes,
        idle_ttl_seconds,
        delete_after_stop_seconds,
    )


def _get_sandbox_create_extra_fields() -> dict[str, Any]:
    """Parse SANDBOX_CREATE_EXTRA_JSON into extra fields merged into the
    sandbox-create request body, e.g. ``{"_internal_runtime": "v2"}``."""
    raw = os.environ.get("SANDBOX_CREATE_EXTRA_JSON")
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


def _install_create_extra_fields(client: AsyncSandboxClient, extra: dict[str, Any]) -> None:
    """Merge ``extra`` into the JSON body of the sandbox-create request.

    The SDK's ``create_sandbox`` builds a fixed payload with no passthrough, so
    wrap the HTTP client's ``post`` to inject the fields on the ``POST /boxes``
    request only (other endpoints post to ``/boxes/{name}/...``).
    """
    if not extra:
        return
    original_post = client._http.post

    async def post_with_extra(url: Any, *args: Any, **kwargs: Any) -> Any:
        payload = kwargs.get("json")
        if str(url).endswith("/boxes") and isinstance(payload, dict):
            kwargs["json"] = {**payload, **extra}
        return await original_post(url, *args, **kwargs)

    client._http.post = post_with_extra


def _github_proxy_rules(github_token: str) -> list[dict[str, Any]]:
    basic_auth = base64.b64encode(f"x-access-token:{github_token}".encode()).decode()
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


def _retry_after_seconds(response: httpx.Response | None) -> float | None:
    if response is None:
        return None
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        delay = float(raw)
    except ValueError:
        return None
    return max(delay, 0.0)


def _is_retryable_proxy_config_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in PROXY_CONFIG_RETRYABLE_STATUS_CODES
    return isinstance(exc, httpx.TransportError)


def _status_text(sandbox_or_status: Any) -> str:
    status = getattr(sandbox_or_status, "status", sandbox_or_status)
    status = getattr(status, "value", status)
    return str(status or "").lower()


def _is_retryable_sandbox_create_error(exc: BaseException) -> bool:
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


async def _wait_for_reconnected_sandbox(
    client: AsyncSandboxClient,
    sandbox_id: str,
    *,
    timeout_seconds: float = SANDBOX_RECONNECT_READY_TIMEOUT_SECONDS,
    poll_seconds: float = SANDBOX_RECONNECT_READY_POLL_SECONDS,
) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last_sandbox = await client.get_sandbox(name=sandbox_id)
    while True:
        status = _status_text(last_sandbox)
        if status in SANDBOX_READY_STATUSES or status not in SANDBOX_RECONNECT_PENDING_STATUSES:
            return last_sandbox
        if asyncio.get_running_loop().time() >= deadline:
            return last_sandbox
        await asyncio.sleep(
            min(poll_seconds, max(deadline - asyncio.get_running_loop().time(), 0.0))
        )
        last_sandbox = await client.get_sandbox(name=sandbox_id)


async def _release_sandbox_name(client: AsyncSandboxClient, name: str | None) -> None:
    """Best-effort delete of any existing sandbox holding ``name``.

    Sandbox names are unique in LangSmith and thread-deterministic, so the only
    box that can hold this name is this thread's own — typically a dead one
    (idle-stopped past its TTL) we're recreating. Provisioning is serialized per
    thread, so this never races a live box. Without this, recreate would 409.
    """
    if not name:
        return
    try:
        await client.delete_sandbox(name)
    except Exception as exc:  # noqa: BLE001 - name is free if nothing to delete
        logger.debug("No pre-existing sandbox %s to release (%s)", name, type(exc).__name__)


async def _create_sandbox_with_retry(
    client: AsyncSandboxClient,
    *,
    snapshot_id: str,
    name: str | None,
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
                name=name,
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
                "Failed to create LangSmith sandbox (%s); retrying in %.1fs",
                type(exc).__name__,
                delay,
            )
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable sandbox retry state")


async def _configure_github_proxy(sandbox_name: str, github_token: str) -> None:
    """Configure sandbox proxy to inject GitHub auth for GitHub traffic.

    Uses the LangSmith proxy-config API to set up header injection so that
    git operations (clone, pull, push) authenticate via the proxy rather than
    writing credentials to disk in the sandbox.

    Args:
        sandbox_name: The sandbox name/ID returned by the LangSmith API.
        github_token: GitHub token to inject as Authorization header.
    """
    api_key = _get_sandbox_api_key()
    if not api_key:
        logger.warning("No LangSmith API key found, skipping GitHub proxy configuration")
        return
    langsmith_endpoint = _get_sandbox_endpoint()
    url = f"{langsmith_endpoint}/v2/sandboxes/boxes/{sandbox_name}"
    payload = {"proxy_config": {"rules": _github_proxy_rules(github_token)}}
    async with httpx.AsyncClient(timeout=PROXY_CONFIG_TIMEOUT_SECONDS) as client:
        for attempt in range(PROXY_CONFIG_MAX_ATTEMPTS):
            try:
                response = await client.patch(
                    url,
                    json=payload,
                    headers={"X-API-Key": api_key},
                )
                response.raise_for_status()
                break
            except Exception as exc:
                if attempt == PROXY_CONFIG_MAX_ATTEMPTS - 1 or not _is_retryable_proxy_config_error(
                    exc
                ):
                    raise
                retry_after = (
                    _retry_after_seconds(exc.response)
                    if isinstance(exc, httpx.HTTPStatusError)
                    else None
                )
                delay = (
                    retry_after
                    or PROXY_CONFIG_RETRY_DELAYS_SECONDS[
                        min(attempt, len(PROXY_CONFIG_RETRY_DELAYS_SECONDS) - 1)
                    ]
                )
                logger.warning(
                    "Failed to configure GitHub proxy for sandbox %s (%s); retrying in %.1fs",
                    sandbox_name,
                    type(exc).__name__,
                    delay,
                )
                await asyncio.sleep(delay)
    logger.info("Configured GitHub proxy for sandbox %s", sandbox_name)


def get_async_sandbox_client() -> AsyncSandboxClient:
    """Build an ``AsyncSandboxClient`` from the resolved sandbox LangSmith credentials."""
    return AsyncSandboxClient(
        api_key=_get_sandbox_api_key(), api_endpoint=_get_sandbox_api_endpoint()
    )


async def create_langsmith_sandbox(
    sandbox_id: str | None = None,
    github_token: str | None = None,
    *,
    snapshot_id: str | None = None,
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
                      falls back to DEFAULT_SANDBOX_SNAPSHOT_ID.

    Returns:
        SandboxBackendProtocol instance
    """
    api_key = _get_sandbox_api_key()
    (
        default_snapshot_id,
        fs_capacity_bytes,
        vcpus,
        mem_bytes,
        idle_ttl_seconds,
        delete_after_stop_seconds,
    ) = _get_sandbox_snapshot_config()

    effective_snapshot_id = snapshot_id or default_snapshot_id

    provider = LangSmithProvider(api_key=api_key)
    backend = await provider.get_or_create(
        sandbox_id=sandbox_id,
        snapshot_id=effective_snapshot_id,
        name=_sandbox_name_for_thread(_current_thread_id()),
        fs_capacity_bytes=fs_capacity_bytes,
        vcpus=vcpus,
        mem_bytes=mem_bytes,
        idle_ttl_seconds=idle_ttl_seconds,
        delete_after_stop_seconds=delete_after_stop_seconds,
    )
    await _update_thread_sandbox_metadata(backend.id)

    if sandbox_id is None and github_token:
        await _configure_github_proxy(backend.id, github_token)

    return backend


async def _update_thread_sandbox_metadata(sandbox_id: str) -> None:
    """Update thread metadata with sandbox_id."""
    try:
        from langgraph_sdk import get_client

        thread_id = _current_thread_id()
        if not thread_id:
            return
        client = get_client()
        await client.threads.update(
            thread_id=thread_id,
            metadata={"sandbox_id": sandbox_id},
        )
    except Exception:
        pass


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
    def _safe_kill(handle: Any) -> None:
        try:
            handle.kill()
        except Exception:  # noqa: BLE001 - best-effort cleanup of a wedged command
            logger.warning("Failed to kill timed-out sandbox command", exc_info=True)

    def _base_execute(self, command: str, timeout: int | None) -> ExecuteResponse:
        # WS path unavailable; the base wait=True path falls back to HTTP,
        # which carries its own request deadline.
        return LangSmithSandbox.execute(self, command, timeout=timeout)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        effective = timeout if timeout is not None else self._default_timeout
        if not effective:  # 0 / None: caller opted out of any deadline
            return super().execute(command, timeout=timeout)
        # run(wait=False) eagerly opens the WS and reads the "started" frame, so
        # connect/setup failures raise here — fall back to the base path.
        try:
            handle = self._sandbox.run(command, timeout=effective, wait=False)
        except (*self._WS_FALLBACK_ERRORS, *SANDBOX_NOT_READY_ERRORS, TimeoutError):
            return self._base_execute(command, timeout)
        deadline = self._deadline(effective)
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sbx-exec")
        try:
            future = pool.submit(lambda: handle.result)
            try:
                result = future.result(timeout=deadline)
            except FuturesTimeout:
                self._safe_kill(handle)
                return self._timeout_response(deadline, server_side=False)
            except CommandTimeoutError:
                return self._timeout_response(effective, server_side=True)
            except (*self._WS_FALLBACK_ERRORS, *SANDBOX_NOT_READY_ERRORS):
                return self._base_execute(command, timeout)
            return self._result_to_response(result)
        finally:
            # Never join: a still-wedged worker must not block the caller.
            pool.shutdown(wait=False)

    async def aexecute(
        self,
        command: str,
        *,
        timeout: int | None = None,  # noqa: ASYNC109 - forwarded semantic timeout, not an asyncio contract
    ) -> ExecuteResponse:
        effective = timeout if timeout is not None else self._default_timeout
        if not effective:
            return await super().aexecute(command, timeout=timeout)
        # run(wait=False) eagerly opens the WS and reads the "started" frame
        # (blocking, bounded by the SDK connect timeout); connect/setup failures
        # raise here — fall back to the base path.
        try:
            handle = await asyncio.to_thread(
                self._sandbox.run, command, timeout=effective, wait=False
            )
        except (*self._WS_FALLBACK_ERRORS, *SANDBOX_NOT_READY_ERRORS, TimeoutError):
            return await asyncio.to_thread(self._base_execute, command, timeout)
        deadline = self._deadline(effective)
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(lambda: handle.result), timeout=deadline
            )
        except TimeoutError:
            await asyncio.to_thread(self._safe_kill, handle)
            return self._timeout_response(deadline, server_side=False)
        except CommandTimeoutError:
            return self._timeout_response(effective, server_side=True)
        except (*self._WS_FALLBACK_ERRORS, *SANDBOX_NOT_READY_ERRORS):
            return await asyncio.to_thread(self._base_execute, command, timeout)
        return self._result_to_response(result)


class SandboxProvider(ABC):
    """Interface for creating and deleting sandbox backends."""

    @abstractmethod
    async def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        **kwargs: Any,
    ) -> SandboxBackendProtocol:
        """Get an existing sandbox, or create one if needed."""
        raise NotImplementedError

    @abstractmethod
    async def delete(
        self,
        *,
        sandbox_id: str,
        **kwargs: Any,
    ) -> None:
        """Delete a sandbox by id."""
        raise NotImplementedError


class LangSmithProvider(SandboxProvider):
    """LangSmith sandbox provider implementation."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or _get_sandbox_api_key()
        self._api_endpoint = _get_sandbox_api_endpoint()
        if not self._api_key:
            msg = "LANGSMITH_API_KEY (or LANGSMITH_API_KEY_PROD) not set"
            raise ValueError(msg)

    @classmethod
    def validate_startup_config(cls) -> None:
        """Validate env-var configuration at server startup. Raises ValueError if invalid."""
        if not os.environ.get("DEFAULT_SANDBOX_SNAPSHOT_ID"):
            msg = "DEFAULT_SANDBOX_SNAPSHOT_ID must be set when SANDBOX_TYPE=langsmith"
            raise ValueError(msg)
        for name in (
            "DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES",
            "DEFAULT_SANDBOX_VCPUS",
            "DEFAULT_SANDBOX_MEM_BYTES",
            "DEFAULT_SANDBOX_IDLE_TTL_SECONDS",
            "DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS",
        ):
            raw = os.environ.get(name)
            if raw is None or raw == "":
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
        name: str | None = None,
        fs_capacity_bytes: int | None = None,
        vcpus: int | None = None,
        mem_bytes: int | None = None,
        idle_ttl_seconds: int | None = None,
        delete_after_stop_seconds: int | None = None,
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
                try:
                    sandbox = await client.get_sandbox(name=sandbox_id)
                except Exception as e:
                    msg = f"Failed to connect to existing sandbox '{sandbox_id}': {e}"
                    raise RuntimeError(msg) from e
                status = _status_text(sandbox)
                if status and status not in SANDBOX_READY_STATUSES:
                    if status in SANDBOX_RECONNECT_STARTABLE_STATUSES:
                        try:
                            logger.info(
                                "Starting LangSmith sandbox %s before reconnect (status=%s)",
                                sandbox_id,
                                status,
                            )
                            await client.start_sandbox(sandbox_id)
                            sandbox = await _wait_for_reconnected_sandbox(client, sandbox_id)
                            status = _status_text(sandbox)
                        except Exception as e:
                            msg = f"Failed to start existing sandbox '{sandbox_id}' ({status})"
                            raise RuntimeError(msg) from e
                    if status not in SANDBOX_READY_STATUSES:
                        msg = f"Existing sandbox '{sandbox_id}' is {status or 'unknown'}, not reusable"
                        raise RuntimeError(msg)
                return TimeoutLangSmithSandbox(sandbox.to_sync())

            if not snapshot_id:
                msg = "DEFAULT_SANDBOX_SNAPSHOT_ID must be set when SANDBOX_TYPE=langsmith"
                raise ValueError(msg)

            _install_create_extra_fields(client, _get_sandbox_create_extra_fields())
            await _release_sandbox_name(client, name)

            try:
                sandbox = await _create_sandbox_with_retry(
                    client,
                    snapshot_id=snapshot_id,
                    name=name,
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

    async def delete(self, *, sandbox_id: str, **kwargs: Any) -> None:
        """Delete a LangSmith sandbox."""
        async with AsyncSandboxClient(
            api_key=self._api_key, api_endpoint=self._api_endpoint
        ) as client:
            await client.delete_sandbox(sandbox_id)
