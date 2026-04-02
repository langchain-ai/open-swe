"""Main entry point and CLI loop for Open SWE agent."""
# ruff: noqa: E402

# Suppress deprecation warnings from langchain_core (e.g., Pydantic V1 on Python 3.14+)
# ruff: noqa: E402
import logging
import os
import warnings

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
from deepagents.backends.protocol import SandboxBackendProtocol
from langsmith.sandbox import SandboxClientError

from .middleware import (
    ToolErrorMiddleware,
    check_message_queue_before_model,
    ensure_no_empty_msg,
    open_pr_if_needed,
)
from .prompt import construct_system_prompt
from .tools import (
    commit_and_open_pr,
    create_pr_review,
    dismiss_pr_review,
    fetch_url,
    get_branch_name,
    get_pr_review,
    github_comment,
    http_request,
    linear_comment,
    linear_create_issue,
    linear_delete_issue,
    linear_get_issue,
    linear_get_issue_comments,
    linear_list_teams,
    linear_update_issue,
    list_pr_review_comments,
    list_pr_reviews,
    list_repos,
    slack_thread_reply,
    submit_pr_review,
    update_pr_review,
    web_search,
)
from .utils.auth import resolve_github_token
from .utils.model import make_model
from .utils.sandbox import create_sandbox

client = get_client()

SANDBOX_CREATING = "__creating__"
SANDBOX_CREATION_TIMEOUT = 180
SANDBOX_POLL_INTERVAL = 1.0

from .utils.github import setup_git_credentials
from .utils.sandbox_state import SANDBOX_BACKENDS, get_sandbox_id_from_metadata


async def _recreate_sandbox(thread_id: str) -> SandboxBackendProtocol:
    """Recreate a sandbox after a connection failure."""
    SANDBOX_BACKENDS.pop(thread_id, None)
    await client.threads.update(
        thread_id=thread_id,
        metadata={"sandbox_id": SANDBOX_CREATING},
    )
    try:
        sandbox_backend = await asyncio.to_thread(create_sandbox)
    except Exception:
        logger.exception("Failed to recreate sandbox after connection failure")
        await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": None})
        raise
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


DEFAULT_LLM_MODEL_ID = "anthropic:claude-opus-4-6"
DEFAULT_RECURSION_LIMIT = 1_000


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

    github_token, new_encrypted = await resolve_github_token(config, thread_id)
    config["metadata"]["github_token_encrypted"] = new_encrypted

    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    sandbox_id = await get_sandbox_id_from_metadata(thread_id)

    if sandbox_id == SANDBOX_CREATING and not sandbox_backend:
        logger.info("Sandbox creation in progress, waiting...")
        sandbox_id = await _wait_for_sandbox_id(thread_id)

    if sandbox_backend:
        logger.info("Using cached sandbox backend for thread %s", thread_id)
        try:
            await asyncio.to_thread(sandbox_backend.execute, "echo ok")
        except SandboxClientError:
            logger.warning(
                "Cached sandbox is no longer reachable for thread %s, recreating",
                thread_id,
            )
            sandbox_backend = await _recreate_sandbox(thread_id)

    elif sandbox_id is None:
        logger.info("Creating new sandbox for thread %s", thread_id)
        await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": SANDBOX_CREATING})

        try:
            sandbox_backend = await asyncio.to_thread(create_sandbox)
            logger.info("Sandbox created: %s", sandbox_backend.id)
        except Exception:
            logger.exception("Failed to create sandbox")
            try:
                await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": None})
                logger.info("Reset sandbox_id to None for thread %s", thread_id)
            except Exception:
                logger.exception("Failed to reset sandbox_id metadata")
            raise
    else:
        logger.info("Connecting to existing sandbox %s", sandbox_id)
        try:
            sandbox_backend = await asyncio.to_thread(create_sandbox, sandbox_id)
            logger.info("Connected to existing sandbox %s", sandbox_id)
        except Exception:
            logger.warning("Failed to connect to existing sandbox %s, creating new one", sandbox_id)
            await client.threads.update(
                thread_id=thread_id,
                metadata={"sandbox_id": SANDBOX_CREATING},
            )

            try:
                sandbox_backend = await asyncio.to_thread(create_sandbox)
                logger.info("New sandbox created: %s", sandbox_backend.id)
            except Exception:
                logger.exception("Failed to create replacement sandbox")
                await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": None})
                raise

    SANDBOX_BACKENDS[thread_id] = sandbox_backend

    await client.threads.update(
        thread_id=thread_id,
        metadata={"sandbox_id": sandbox_backend.id},
    )

    if github_token:
        await asyncio.to_thread(setup_git_credentials, sandbox_backend, github_token)
        await asyncio.to_thread(
            sandbox_backend.execute,
            "git config --global credential.helper 'store --file=/tmp/.git-credentials'",
        )
    await asyncio.to_thread(
        sandbox_backend.execute,
        "git config --global user.name 'open-swe[bot]' && git config --global user.email 'open-swe@users.noreply.github.com'",
    )

    linear_issue = config["configurable"].get("linear_issue", {})
    linear_project_id = linear_issue.get("linear_project_id", "")
    linear_issue_number = linear_issue.get("linear_issue_number", "")

    logger.info("Returning agent with sandbox for thread %s", thread_id)
    return create_deep_agent(
        model=make_model(
            os.environ.get("LLM_MODEL_ID", DEFAULT_LLM_MODEL_ID),
            temperature=0,
            max_tokens=20_000,
        ),
        system_prompt=construct_system_prompt(
            linear_project_id=linear_project_id,
            linear_issue_number=linear_issue_number,
        ),
        tools=[
            http_request,
            fetch_url,
            web_search,
            list_repos,
            get_branch_name,
            commit_and_open_pr,
            linear_comment,
            linear_create_issue,
            linear_delete_issue,
            linear_get_issue,
            linear_get_issue_comments,
            linear_list_teams,
            linear_update_issue,
            slack_thread_reply,
            github_comment,
            list_pr_reviews,
            get_pr_review,
            create_pr_review,
            update_pr_review,
            dismiss_pr_review,
            submit_pr_review,
            list_pr_review_comments,
        ],
        backend=sandbox_backend,
        middleware=[
            ToolErrorMiddleware(),
            check_message_queue_before_model,
            ensure_no_empty_msg,
            open_pr_if_needed,
        ],
    ).with_config(config)
