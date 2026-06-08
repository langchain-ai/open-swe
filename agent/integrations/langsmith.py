"""LangSmith sandbox backend integration."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any

import httpx
from deepagents.backends import LangSmithSandbox
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol
from langsmith.sandbox import (
    CommandTimeoutError,
    SandboxClient,
    SandboxConnectionError,
    SandboxServerReloadError,
)

logger = logging.getLogger(__name__)

DEFAULT_SNAPSHOT_FS_CAPACITY_BYTES = 32 * 1024**3
DEFAULT_SANDBOX_VCPUS = 2
DEFAULT_SANDBOX_MEM_BYTES = 7936 * 1024**2  # 7936 MiB ("large" tier cap)
DEFAULT_SANDBOX_IDLE_TTL_SECONDS = 10 * 60  # 10 minutes
DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS = 24 * 60 * 60  # 24 hours
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


def _configure_github_proxy(sandbox_name: str, github_token: str) -> None:
    """Configure sandbox proxy to inject GitHub auth for GitHub traffic.

    Uses the LangSmith proxy-config API to set up header injection so that
    git operations (clone, pull, push) authenticate via the proxy rather than
    writing credentials to disk in the sandbox.

    Args:
        sandbox_name: The sandbox name/ID returned by the LangSmith API.
        github_token: GitHub token to inject as Authorization header.
    """
    api_key = _get_langsmith_api_key()
    if not api_key:
        logger.warning("No LangSmith API key found, skipping GitHub proxy configuration")
        return
    langsmith_endpoint = os.environ.get("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    url = f"{langsmith_endpoint}/v2/sandboxes/boxes/{sandbox_name}"
    payload = {"proxy_config": {"rules": _github_proxy_rules(github_token)}}
    with httpx.Client(timeout=PROXY_CONFIG_TIMEOUT_SECONDS) as client:
        for attempt in range(PROXY_CONFIG_MAX_ATTEMPTS):
            try:
                response = client.patch(
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
                time.sleep(delay)
    logger.info("Configured GitHub proxy for sandbox %s", sandbox_name)


def create_langsmith_sandbox(
    sandbox_id: str | None = None,
    github_token: str | None = None,
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

    Returns:
        SandboxBackendProtocol instance
    """
    api_key = _get_langsmith_api_key()
    (
        snapshot_id,
        fs_capacity_bytes,
        vcpus,
        mem_bytes,
        idle_ttl_seconds,
        delete_after_stop_seconds,
    ) = _get_sandbox_snapshot_config()

    provider = LangSmithProvider(api_key=api_key)
    backend = provider.get_or_create(
        sandbox_id=sandbox_id,
        snapshot_id=snapshot_id,
        fs_capacity_bytes=fs_capacity_bytes,
        vcpus=vcpus,
        mem_bytes=mem_bytes,
        idle_ttl_seconds=idle_ttl_seconds,
        delete_after_stop_seconds=delete_after_stop_seconds,
    )
    _update_thread_sandbox_metadata(backend.id)

    if sandbox_id is None and github_token:
        _configure_github_proxy(backend.id, github_token)

    return backend


def _update_thread_sandbox_metadata(sandbox_id: str) -> None:
    """Update thread metadata with sandbox_id."""
    try:
        import asyncio

        from langgraph.config import get_config
        from langgraph_sdk import get_client

        config = get_config()
        thread_id = config.get("configurable", {}).get("thread_id")
        if not thread_id:
            return
        client = get_client()

        async def _update() -> None:
            await client.threads.update(
                thread_id=thread_id,
                metadata={"sandbox_id": sandbox_id},
            )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_update())
        else:
            loop.create_task(_update())
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
        except (*self._WS_FALLBACK_ERRORS, TimeoutError):
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
            except self._WS_FALLBACK_ERRORS:
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
        except (*self._WS_FALLBACK_ERRORS, TimeoutError):
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
        except self._WS_FALLBACK_ERRORS:
            return await asyncio.to_thread(self._base_execute, command, timeout)
        return self._result_to_response(result)


class SandboxProvider(ABC):
    """Interface for creating and deleting sandbox backends."""

    @abstractmethod
    def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        **kwargs: Any,
    ) -> SandboxBackendProtocol:
        """Get an existing sandbox, or create one if needed."""
        raise NotImplementedError

    @abstractmethod
    def delete(
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
        from langsmith import sandbox

        self._api_key = api_key or _get_langsmith_api_key()
        if not self._api_key:
            msg = "LANGSMITH_API_KEY (or LANGSMITH_API_KEY_PROD) not set"
            raise ValueError(msg)
        self._client: SandboxClient = sandbox.SandboxClient(api_key=self._api_key)

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

    def get_or_create(
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
        **kwargs: Any,
    ) -> SandboxBackendProtocol:
        """Get existing or create new LangSmith sandbox."""
        if kwargs:
            msg = f"Received unsupported arguments: {list(kwargs.keys())}"
            raise TypeError(msg)
        if sandbox_id:
            try:
                sandbox = self._client.get_sandbox(name=sandbox_id)
            except Exception as e:
                msg = f"Failed to connect to existing sandbox '{sandbox_id}': {e}"
                raise RuntimeError(msg) from e
            return TimeoutLangSmithSandbox(sandbox)

        if not snapshot_id:
            msg = "DEFAULT_SANDBOX_SNAPSHOT_ID must be set when SANDBOX_TYPE=langsmith"
            raise ValueError(msg)

        try:
            sandbox = self._client.create_sandbox(
                snapshot_id=snapshot_id,
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

        return TimeoutLangSmithSandbox(sandbox)

    def delete(self, *, sandbox_id: str, **kwargs: Any) -> None:
        """Delete a LangSmith sandbox."""
        self._client.delete_sandbox(sandbox_id)
