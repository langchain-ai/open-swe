"""Main entry point and CLI loop for Open SWE agent."""
# ruff: noqa: E402

# Suppress deprecation warnings from langchain_core (e.g., Pydantic V1 on Python 3.14+)
# ruff: noqa: E402
import logging
import os
import warnings
from typing import Any

logger = logging.getLogger(__name__)

from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel
from langgraph_sdk import get_client

warnings.filterwarnings("ignore", module="langchain_core._api.deprecation")

import asyncio

# Suppress Pydantic v1 compatibility warnings from langchain on Python 3.14+
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)

# Now safe to import agent (which imports LangChain modules)
from deepagents import create_deep_agent
from deepagents.backends import LangSmithSandbox
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware import ModelCallLimitMiddleware
from langsmith.sandbox import SandboxClientError

from .dashboard.agent_overrides import (
    load_profile,
    normalize_profile_overrides,
    resolve_github_login,
)
from .integrations.langsmith import _configure_github_proxy
from .middleware import (
    ModelFallbackMiddleware,
    SandboxCircuitBreakerMiddleware,
    SanitizeToolInputsMiddleware,
    SlackAssistantStatusMiddleware,
    ToolErrorMiddleware,
    check_message_queue_before_model,
    ensure_no_empty_msg,
    notify_step_limit_reached,
)
from .prompt import construct_system_prompt
from .tools import (
    fetch_url,
    http_request,
    linear_comment,
    linear_create_issue,
    linear_delete_issue,
    linear_get_issue,
    linear_get_issue_comments,
    linear_list_teams,
    linear_update_issue,
    request_pr_review,
    slack_read_thread_messages,
    slack_thread_reply,
    web_search,
)
from .utils.auth import resolve_github_token
from .utils.authorship import resolve_triggering_user_identity
from .utils.github_app import get_github_app_installation_token
from .utils.model import (
    DEFAULT_LLM_REASONING,
    ModelKwargs,
    fallback_model_id_for,
    make_model,
    provider_model_kwargs,
)
from .utils.sandbox import create_sandbox
from .utils.sandbox_paths import aresolve_sandbox_work_dir

client = get_client()

SANDBOX_CREATING = "__creating__"
SANDBOX_CREATION_TIMEOUT = 180
SANDBOX_POLL_INTERVAL = 1.0

from .utils.sandbox_state import (
    SANDBOX_BACKENDS,
    get_sandbox_id_from_metadata,
    set_sandbox_backend,
    unwrap_sandbox_backend,
)


async def _start_langsmith_sandbox_if_needed(sandbox_backend: SandboxBackendProtocol) -> None:
    """Start a LangSmith sandbox before operations that require it to be running."""
    if os.getenv("SANDBOX_TYPE", "langsmith") != "langsmith":
        return
    current_backend = unwrap_sandbox_backend(sandbox_backend)
    if not isinstance(current_backend, LangSmithSandbox):
        return

    sandbox = current_backend._sandbox  # noqa: SLF001
    status = await asyncio.to_thread(sandbox._client.get_sandbox_status, sandbox.name)  # noqa: SLF001
    status_name = getattr(status, "status", status)
    status_name = getattr(status_name, "value", status_name)
    status_text = str(status_name or "").lower()
    if status_text in {"running", "ready"}:
        return

    logger.info(
        "Starting LangSmith sandbox %s before proxy refresh (status=%s)",
        current_backend.id,
        status_text or "unknown",
    )
    await asyncio.to_thread(sandbox.start)


async def _create_sandbox_with_proxy() -> SandboxBackendProtocol:
    """Create a new sandbox with GitHub proxy auth configured.

    Uses create_sandbox (generic factory) so non-langsmith providers still work.
    For langsmith sandboxes, configures the proxy with the installation token.
    """
    sandbox_backend = await asyncio.to_thread(create_sandbox)

    sandbox_type = os.getenv("SANDBOX_TYPE", "langsmith")
    if sandbox_type == "langsmith":
        installation_token = await get_github_app_installation_token()
        if not installation_token:
            msg = "Cannot configure proxy: GitHub App installation token is unavailable"
            logger.error(msg)
            raise ValueError(msg)
        await _start_langsmith_sandbox_if_needed(sandbox_backend)
        await asyncio.to_thread(_configure_github_proxy, sandbox_backend.id, installation_token)

    return sandbox_backend


async def _refresh_github_proxy(
    sandbox_backend: SandboxBackendProtocol,
) -> None:
    """Refresh GitHub proxy credentials for reused LangSmith sandboxes."""
    if os.getenv("SANDBOX_TYPE", "langsmith") != "langsmith":
        return

    installation_token = await get_github_app_installation_token()
    if not installation_token:
        logger.warning(
            "Skipping GitHub proxy refresh for sandbox %s: installation token unavailable",
            sandbox_backend.id,
        )
        return

    current_backend = unwrap_sandbox_backend(sandbox_backend)
    await _start_langsmith_sandbox_if_needed(current_backend)
    await asyncio.to_thread(_configure_github_proxy, current_backend.id, installation_token)


async def _refresh_github_proxy_or_recreate(
    sandbox_backend: SandboxBackendProtocol,
    thread_id: str,
) -> SandboxBackendProtocol:
    """Refresh proxy credentials, recreating stale LangSmith sandboxes on failure."""
    try:
        await _refresh_github_proxy(sandbox_backend)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to refresh GitHub proxy for sandbox %s on thread %s, recreating sandbox",
            sandbox_backend.id,
            thread_id,
            exc_info=True,
        )
        return await _recreate_sandbox(thread_id)
    return sandbox_backend


async def _configure_git_identity(sandbox_backend: SandboxBackendProtocol) -> None:
    await asyncio.to_thread(
        sandbox_backend.execute,
        "git config --global user.name 'open-swe[bot]' && "
        "git config --global user.email 'open-swe@users.noreply.github.com'",
    )


async def _recreate_sandbox(thread_id: str) -> SandboxBackendProtocol:
    """Recreate a sandbox after a connection failure.

    Sets the SANDBOX_CREATING sentinel and creates a fresh sandbox
    (with proxy auth configured), swapping the per-thread proxy target.
    The agent is responsible for cloning repos via tools.
    """
    await client.threads.update(
        thread_id=thread_id,
        metadata={"sandbox_id": SANDBOX_CREATING},
    )
    try:
        sandbox_backend = set_sandbox_backend(thread_id, await _create_sandbox_with_proxy())
    except Exception:
        logger.exception("Failed to recreate sandbox after connection failure")
        await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": None})
        raise
    return sandbox_backend


async def check_or_recreate_sandbox(
    sandbox_backend: SandboxBackendProtocol, thread_id: str
) -> SandboxBackendProtocol:
    """Check if a cached sandbox is reachable; recreate it if not.

    Pings the sandbox with a lightweight command. If the sandbox is
    unreachable (SandboxClientError), it is torn down and a fresh one
    is created via _recreate_sandbox.

    Returns the original backend if healthy, or a new one if recreated.
    """
    try:
        await asyncio.to_thread(sandbox_backend.execute, "echo ok")
    except SandboxClientError:
        logger.warning(
            "Cached sandbox is no longer reachable for thread %s, recreating",
            thread_id,
        )
        sandbox_backend = await _recreate_sandbox(thread_id)
    return sandbox_backend


async def _wait_for_sandbox_id(thread_id: str) -> str:
    """Wait for sandbox_id to be set in thread metadata.

    Polls thread metadata until sandbox_id is set to a real value
    (not the creating sentinel).

    Raises:
        TimeoutError: If sandbox creation takes too long
    """
    elapsed = 0.0
    while elapsed < SANDBOX_CREATION_TIMEOUT:
        sandbox_id = await get_sandbox_id_from_metadata(thread_id)
        if sandbox_id is not None and sandbox_id != SANDBOX_CREATING:
            return sandbox_id
        await asyncio.sleep(SANDBOX_POLL_INTERVAL)
        elapsed += SANDBOX_POLL_INTERVAL

    msg = f"Timeout waiting for sandbox creation for thread {thread_id}"
    raise TimeoutError(msg)


def graph_loaded_for_execution(config: RunnableConfig) -> bool:
    """Check if the graph is loaded for actual execution vs introspection."""
    return (
        config["configurable"].get("__is_for_execution__", False)
        if "configurable" in config
        else False
    )


async def ensure_sandbox_for_thread(thread_id: str) -> SandboxBackendProtocol:
    """Get-or-create a healthy sandbox bound to ``thread_id``.

    Implements the four-state lifecycle described in AGENTS.md:

    1. Cached in memory → ping; recreate on ``SandboxClientError``.
    2. Metadata says ``__creating__`` and no cache → poll until ready.
    3. No sandbox at all → create one and persist the id.
    4. Metadata has an id but no cache → reconnect; recreate on failure.

    For LangSmith sandboxes, also refreshes the GitHub App proxy auth.
    Persists the resulting ``sandbox_id`` to thread metadata, and on the
    first creation/reconnect for this thread initializes git identity.
    """
    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    sandbox_id = await get_sandbox_id_from_metadata(thread_id)

    if sandbox_id == SANDBOX_CREATING and not sandbox_backend:
        logger.info("Sandbox creation in progress for thread %s, waiting...", thread_id)
        sandbox_id = await _wait_for_sandbox_id(thread_id)

    if sandbox_backend:
        logger.info("Using cached sandbox backend for thread %s", thread_id)
        original_sandbox_id = sandbox_backend.id
        sandbox_backend = await check_or_recreate_sandbox(sandbox_backend, thread_id)
        if sandbox_backend.id == original_sandbox_id:
            sandbox_backend = await _refresh_github_proxy_or_recreate(sandbox_backend, thread_id)
    elif sandbox_id is None:
        logger.info("Creating new sandbox for thread %s", thread_id)
        await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": SANDBOX_CREATING})
        try:
            sandbox_backend = await _create_sandbox_with_proxy()
            logger.info("Sandbox created: %s", sandbox_backend.id)
        except Exception:
            logger.exception("Failed to create sandbox")
            try:
                await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": None})
            except Exception:
                logger.exception("Failed to reset sandbox_id metadata")
            raise
    else:
        logger.info("Connecting to existing sandbox %s", sandbox_id)
        created_replacement_sandbox = False
        try:
            sandbox_backend = await asyncio.to_thread(create_sandbox, sandbox_id)
        except Exception:
            logger.warning("Failed to connect to existing sandbox %s, creating new one", sandbox_id)
            await client.threads.update(
                thread_id=thread_id, metadata={"sandbox_id": SANDBOX_CREATING}
            )
            try:
                sandbox_backend = await _create_sandbox_with_proxy()
                created_replacement_sandbox = True
            except Exception:
                logger.exception("Failed to create replacement sandbox")
                await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": None})
                raise
        if not created_replacement_sandbox:
            original_sandbox_id = sandbox_backend.id
            sandbox_backend = await check_or_recreate_sandbox(sandbox_backend, thread_id)
            if sandbox_backend.id == original_sandbox_id:
                sandbox_backend = await _refresh_github_proxy_or_recreate(
                    sandbox_backend, thread_id
                )

    sandbox_backend = set_sandbox_backend(thread_id, sandbox_backend)

    if sandbox_id != sandbox_backend.id:
        await client.threads.update(
            thread_id=thread_id, metadata={"sandbox_id": sandbox_backend.id}
        )

    # Re-apply git identity every run: cached/reconnected sandboxes may have
    # lost their `--global` config (or had it overwritten), and Vercel preview
    # deploys reject commits whose author email can't be resolved to a GitHub
    # account.
    await _configure_git_identity(sandbox_backend)

    return sandbox_backend


DEFAULT_LLM_MODEL_ID = "openai:gpt-5.5"
DEFAULT_LLM_MAX_TOKENS = 64_000
DEFAULT_RECURSION_LIMIT = 9_999
MODEL_CALL_RECURSION_LIMIT = 5_000  # ~half the recursion limit to account for tool calls


def _get_cached_sandbox_backend(thread_id: str) -> SandboxBackendProtocol:
    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    if sandbox_backend is None:
        raise RuntimeError(f"No sandbox backend cached for thread {thread_id}")
    return sandbox_backend


async def get_agent(config: RunnableConfig) -> Pregel:
    """Get or create an agent with a sandbox for the given thread."""
    thread_id = config["configurable"].get("thread_id", None)

    config["recursion_limit"] = DEFAULT_RECURSION_LIMIT

    if thread_id is None or not graph_loaded_for_execution(config):
        logger.info("No thread_id or not for execution, returning agent without sandbox")
        return create_deep_agent(
            system_prompt="",
            tools=[],
        ).with_config(config)

    github_token, new_encrypted, new_expires_at = await resolve_github_token(config, thread_id)
    config["metadata"]["github_token_encrypted"] = new_encrypted
    config["metadata"]["github_token_expires_at"] = new_expires_at
    triggering_user_identity = await asyncio.to_thread(
        resolve_triggering_user_identity, config, github_token
    )
    del github_token

    sandbox_backend = await ensure_sandbox_for_thread(thread_id)

    linear_issue = config["configurable"].get("linear_issue", {})
    linear_project_id = linear_issue.get("linear_project_id", "")
    linear_issue_number = linear_issue.get("linear_issue_number", "")

    work_dir = await aresolve_sandbox_work_dir(sandbox_backend)

    def backend_factory(_runtime: object, _thread_id: str = thread_id) -> SandboxBackendProtocol:
        return _get_cached_sandbox_backend(_thread_id)

    model_id = os.environ.get("LLM_MODEL_ID", DEFAULT_LLM_MODEL_ID)
    profile_effort: str | None = None
    profile_login = resolve_github_login(config)
    if profile_login:
        profile = await load_profile(profile_login)
        if profile:
            overridden_model, overridden_effort = normalize_profile_overrides(profile)
            if overridden_model:
                logger.info(
                    "Applying dashboard profile override for %s: model=%s effort=%s",
                    profile_login,
                    overridden_model,
                    overridden_effort,
                )
                model_id = overridden_model
                profile_effort = overridden_effort

    model_kwargs = provider_model_kwargs(
        model_id,
        profile_effort,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
    )

    fallback_model_id = os.environ.get("LLM_FALLBACK_MODEL_ID") or fallback_model_id_for(model_id)
    fallback_middleware: list[Any] = []
    if fallback_model_id and fallback_model_id != model_id:
        fallback_kwargs: ModelKwargs = {"max_tokens": DEFAULT_LLM_MAX_TOKENS}
        if fallback_model_id.startswith("openai:"):
            fallback_kwargs["reasoning"] = DEFAULT_LLM_REASONING
        fallback_middleware.append(
            ModelFallbackMiddleware(make_model(fallback_model_id, **fallback_kwargs))
        )
        logger.info("Configured model fallback %s -> %s", model_id, fallback_model_id)

    logger.info("Returning agent with sandbox for thread %s", thread_id)
    return create_deep_agent(
        model=make_model(model_id, **model_kwargs),
        system_prompt=construct_system_prompt(
            working_dir=work_dir,
            linear_project_id=linear_project_id,
            linear_issue_number=linear_issue_number,
            triggering_user_identity=triggering_user_identity,
        ),
        tools=[
            http_request,
            fetch_url,
            web_search,
            linear_comment,
            linear_create_issue,
            linear_delete_issue,
            linear_get_issue,
            linear_get_issue_comments,
            linear_list_teams,
            linear_update_issue,
            request_pr_review,
            slack_read_thread_messages,
            slack_thread_reply,
        ],
        backend=backend_factory,
        middleware=[
            SanitizeToolInputsMiddleware(),
            ModelCallLimitMiddleware(run_limit=MODEL_CALL_RECURSION_LIMIT, exit_behavior="end"),
            ToolErrorMiddleware(),
            check_message_queue_before_model,
            SlackAssistantStatusMiddleware(),
            ensure_no_empty_msg,
            notify_step_limit_reached,
            SandboxCircuitBreakerMiddleware(),
            *fallback_middleware,
        ],
    ).with_config(config)
