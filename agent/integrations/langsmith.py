"""LangSmith sandbox backend integration."""

import asyncio
import base64
import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx
from deepagents.backends import LangSmithSandbox
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol
from langsmith.sandbox import (
    AsyncSandboxClient,
    CommandTimeoutError,
    ResourceNotFoundError,
    SandboxConnectionError,
    SandboxServerReloadError,
)

from agent.config import (
    default_sandbox_snapshot_id,
    langsmith_credentials,
    sandbox_create_extra_fields,
    sandbox_delete_after_stop_seconds,
    sandbox_execute_client_grace_seconds,
    sandbox_fs_capacity_bytes,
    sandbox_idle_ttl_seconds,
    sandbox_langsmith_endpoint,
    sandbox_mem_bytes,
    sandbox_vcpus,
)
from agent.utils.sandbox import SandboxGoneError

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
PROXY_CONFIG_MAX_ATTEMPTS = 3
PROXY_CONFIG_TIMEOUT_SECONDS = 10.0
PROXY_CONFIG_RETRY_DELAYS_SECONDS = (0.5, 1.0)
PROXY_CONFIG_RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
PROXY_CONFIG_NOT_READY_STATUS = 400
PROXY_CONFIG_ERROR_BODY_CHARS = 500
SANDBOX_START_TIMEOUT_SECONDS = 120
PROXY_GH_TOKEN_PLACEHOLDER = "proxy-injected"


def _get_sandbox_api_key() -> str | None:
    """LangSmith API key for sandbox operations."""
    credentials = langsmith_credentials("sandbox")
    return credentials[0] if credentials else None


def _get_sandbox_api_endpoint() -> str:
    """Sandbox API base URL for the langsmith SDK clients.

    The SDK's ``api_endpoint`` is the sandbox base (root + ``/v2/sandboxes``),
    not the API root, and its methods append ``/boxes``, ``/snapshots``, etc.
    """
    root = sandbox_langsmith_endpoint().rstrip("/")
    suffix = "/v2/sandboxes"
    return root if root.endswith(suffix) else f"{root}{suffix}"


def _execute_client_grace_seconds() -> int:
    """Extra wall-clock seconds the client waits past a command's own timeout
    before giving up and killing it. The server is meant to enforce the command
    timeout; this is the client-side backstop for when it doesn't."""
    return sandbox_execute_client_grace_seconds(30)


def _get_sandbox_snapshot_config() -> tuple[str | None, int, int, int, int, int]:
    """Get sandbox snapshot configuration from environment."""
    return (
        default_sandbox_snapshot_id(),
        sandbox_fs_capacity_bytes(DEFAULT_SNAPSHOT_FS_CAPACITY_BYTES),
        sandbox_vcpus(DEFAULT_SANDBOX_VCPUS),
        sandbox_mem_bytes(DEFAULT_SANDBOX_MEM_BYTES),
        sandbox_idle_ttl_seconds(DEFAULT_SANDBOX_IDLE_TTL_SECONDS),
        sandbox_delete_after_stop_seconds(DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS),
    )


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
                "Failed to create LangSmith sandbox (%s); retrying in %.1fs",
                type(exc).__name__,
                delay,
            )
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable sandbox retry state")


def _with_response_body(exc: BaseException) -> httpx.HTTPStatusError | None:
    """Re-raisable copy of ``exc`` carrying the response body, or ``None`` to re-raise as-is.

    ``raise_for_status`` builds its message from the status line and an MDN link
    only, so the API's own explanation of a rejection never reaches the logs.
    """
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    body = exc.response.text.strip()[:PROXY_CONFIG_ERROR_BODY_CHARS]
    if not body:
        return None
    return httpx.HTTPStatusError(
        f"{exc}\nResponse body: {body}",
        request=exc.request,
        response=exc.response,
    )


async def _patch_proxy_config(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    api_key: str,
    sandbox_name: str,
) -> None:
    for attempt in range(PROXY_CONFIG_MAX_ATTEMPTS):
        try:
            response = await client.patch(
                url,
                json=payload,
                headers={"X-API-Key": api_key},
            )
            response.raise_for_status()
            return
        except Exception as exc:
            if attempt == PROXY_CONFIG_MAX_ATTEMPTS - 1 or not _is_retryable_proxy_config_error(
                exc
            ):
                enriched = _with_response_body(exc)
                if enriched is not None:
                    raise enriched from exc
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


async def _start_sandbox_best_effort(sandbox_name: str) -> None:
    """Start ``sandbox_name`` so a proxy-config update can land on it.

    The API rejects a proxy-config update on any sandbox that is not ``ready``,
    and an idle sandbox is stopped rather than deleted — its filesystem, and the
    agent's uncommitted work with it, comes back when the box starts again.
    Failures are logged and swallowed: the retried update reports the real state.
    """
    client = get_async_sandbox_client()
    try:
        await client.start_sandbox(sandbox_name, timeout=SANDBOX_START_TIMEOUT_SECONDS)
        logger.info("Started sandbox %s before retrying GitHub proxy config", sandbox_name)
    except Exception:
        logger.warning("Failed to start sandbox %s", sandbox_name, exc_info=True)
    finally:
        await client.aclose()


async def configure_github_proxy(sandbox_name: str, github_token: str) -> None:
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
    url = f"{sandbox_langsmith_endpoint()}/v2/sandboxes/boxes/{sandbox_name}"
    payload = {"proxy_config": {"rules": _github_proxy_rules(github_token)}}
    async with httpx.AsyncClient(timeout=PROXY_CONFIG_TIMEOUT_SECONDS) as client:
        try:
            await _patch_proxy_config(client, url, payload, api_key, sandbox_name)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != PROXY_CONFIG_NOT_READY_STATUS:
                raise
            logger.warning(
                "Proxy config rejected for sandbox %s; starting it and retrying: %s",
                sandbox_name,
                exc,
            )
            await _start_sandbox_best_effort(sandbox_name)
            await _patch_proxy_config(client, url, payload, api_key, sandbox_name)
    logger.info("Configured GitHub proxy for sandbox %s", sandbox_name)


def get_async_sandbox_client() -> AsyncSandboxClient:
    """Build an ``AsyncSandboxClient`` from the resolved sandbox LangSmith credentials."""
    return AsyncSandboxClient(
        api_key=_get_sandbox_api_key(), api_endpoint=_get_sandbox_api_endpoint()
    )


async def connect_async_langsmith_sandbox(sandbox_id: str) -> tuple[AsyncSandboxClient, Any]:
    client = get_async_sandbox_client()
    try:
        return client, await client.get_sandbox(name=sandbox_id)
    except Exception:
        await client.aclose()
        raise


async def create_langsmith_sandbox(
    sandbox_id: str | None = None,
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
        fs_capacity_bytes=fs_capacity_bytes,
        vcpus=vcpus,
        mem_bytes=mem_bytes,
        idle_ttl_seconds=idle_ttl_seconds,
        delete_after_stop_seconds=delete_after_stop_seconds,
    )

    return backend


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

    async def _abase_execute(self, command: str, timeout: int | None) -> ExecuteResponse:
        return await LangSmithSandbox.aexecute(self, command, timeout=timeout)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError("TimeoutLangSmithSandbox is async-only; use aexecute.")

    async def aexecute(
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
            handle = await self._aget_sandbox().run(command, timeout=effective, wait=False)
        except (*self._WS_FALLBACK_ERRORS, *SANDBOX_NOT_READY_ERRORS, TimeoutError):
            return await self._abase_execute(command, timeout)
        deadline = self._deadline(effective)
        try:
            result = await asyncio.wait_for(handle.result, timeout=deadline)
        except TimeoutError:
            await self._asafe_kill(handle)
            return self._timeout_response(deadline, server_side=False)
        except CommandTimeoutError:
            return self._timeout_response(effective, server_side=True)
        except (*self._WS_FALLBACK_ERRORS, *SANDBOX_NOT_READY_ERRORS):
            return await self._abase_execute(command, timeout)
        return self._result_to_response(result)


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
        self._api_key = api_key or _get_sandbox_api_key()
        self._api_endpoint = _get_sandbox_api_endpoint()
        if not self._api_key:
            msg = "LANGSMITH_API_KEY (or LANGSMITH_API_KEY_PROD) not set"
            raise ValueError(msg)

    @classmethod
    def validate_startup_config(cls) -> None:
        """Validate env-var configuration at server startup. Raises ValueError if invalid.

        Reading every setting is the validation: each accessor raises on a value
        it cannot use.
        """
        if not default_sandbox_snapshot_id():
            # Not fatal: an admin can set the base snapshot at runtime from the
            # dashboard, which is stored outside the environment.
            logger.warning(
                "DEFAULT_SANDBOX_SNAPSHOT_ID is not set; sandbox creation will fail until a "
                "base snapshot is configured in admin settings"
            )
        _get_sandbox_snapshot_config()
        _execute_client_grace_seconds()
        sandbox_create_extra_fields()

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

            if not snapshot_id:
                msg = (
                    "No base snapshot configured: set it in admin settings or via "
                    "DEFAULT_SANDBOX_SNAPSHOT_ID"
                )
                raise ValueError(msg)

            _install_create_extra_fields(client, sandbox_create_extra_fields())

            try:
                sandbox = await _create_sandbox_with_retry(
                    client,
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

            return TimeoutLangSmithSandbox(sandbox.to_sync())
