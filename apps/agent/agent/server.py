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

# Local import for encryption
from langchain_anthropic import ChatAnthropic

from .encryption import decrypt_token
from .middleware import (
    ToolErrorMiddleware,
    check_message_queue_before_model,
    open_pr_if_needed,
    post_to_linear_after_model,
)
from .prompt import construct_system_prompt
from .tools import commit_and_open_pr, fetch_url, http_request
from .integrations.langsmith import _create_langsmith_sandbox


client = get_client()

SANDBOX_CREATING = "__creating__"
SANDBOX_CREATION_TIMEOUT = 180
SANDBOX_POLL_INTERVAL = 1.0

from .utils.sandbox_state import SANDBOX_BACKENDS


async def _clone_or_pull_repo_in_sandbox(  # noqa: PLR0915
    sandbox_backend: SandboxBackendProtocol,
    owner: str,
    repo: str,
    github_token: str | None = None,
) -> str:
    """Clone a GitHub repo into the sandbox, or pull if it already exists.

    Args:
        sandbox_backend: The sandbox backend to execute commands in (LangSmithBackend)
        owner: GitHub repo owner
        repo: GitHub repo name
        github_token: GitHub access token (from agent auth or env var)

    Returns:
        Path to the cloned/updated repo directory
    """
    logger.info("_clone_or_pull_repo_in_sandbox called for %s/%s", owner, repo)
    loop = asyncio.get_event_loop()

    token = github_token
    if not token:
        msg = "No GitHub token provided"
        logger.error(msg)
        raise ValueError(msg)

    repo_dir = f"/workspace/{repo}"

    logger.debug("Checking if repo already exists at %s", repo_dir)
    try:
        dir_exists_result = await loop.run_in_executor(
            None, sandbox_backend.execute, f"test -d {repo_dir} && echo exists"
        )
        check_result = await loop.run_in_executor(
            None, sandbox_backend.execute, f"test -d {repo_dir}/.git && echo exists"
        )
        logger.debug(
            "Repo dir check: exit_code=%s, output=%s",
            dir_exists_result.exit_code,
            dir_exists_result.output[:200] if dir_exists_result.output else "",
        )
        logger.debug(
            "Git check: exit_code=%s, output=%s",
            check_result.exit_code,
            check_result.output[:200] if check_result.output else "",
        )
    except Exception:
        logger.exception("Failed to execute check command in sandbox")
        raise

    if check_result.exit_code == 0 and "exists" in check_result.output:
        logger.info("Repo already exists at %s, pulling latest changes", repo_dir)
        try:
            status_result = await loop.run_in_executor(
                None, sandbox_backend.execute, f"cd {repo_dir} && git status --porcelain"
            )
            logger.debug("Git status result: exit_code=%s", status_result.exit_code)
        except Exception:
            logger.exception("Failed to get git status")
            raise

        # CRITICAL: Ensure remote URL doesn't contain token (clean up from previous runs)
        clean_url = f"https://github.com/{owner}/{repo}.git"
        try:
            await loop.run_in_executor(
                None,
                sandbox_backend.execute,
                f"cd {repo_dir} && git remote set-url origin {clean_url}",
            )
        except Exception:
            logger.exception("Failed to set remote URL")
            raise

        if status_result.exit_code == 0 and not status_result.output.strip():
            auth_url = f"https://git:{token}@github.com/{owner}/{repo}.git"
            try:
                pull_result = await loop.run_in_executor(
                    None, sandbox_backend.execute, f"cd {repo_dir} && git pull {auth_url}"
                )
                logger.debug("Git pull result: exit_code=%s", pull_result.exit_code)
                if pull_result.exit_code != 0:
                    logger.warning(
                        "Git pull failed with exit code %s: %s",
                        pull_result.exit_code,
                        pull_result.output[:200] if pull_result.output else "",
                    )
            except Exception:
                logger.exception("Failed to execute git pull")
                raise
    else:
        if dir_exists_result.exit_code == 0 and "exists" in dir_exists_result.output:
            logger.warning(
                "Repo directory %s exists but is not a git repo; re-initializing in place",
                repo_dir,
            )
            clean_url = f"https://github.com/{owner}/{repo}.git"
            auth_url = f"https://git:{token}@github.com/{owner}/{repo}.git"
            try:
                await loop.run_in_executor(
                    None, sandbox_backend.execute, f"cd {repo_dir} && git init"
                )
            except Exception:
                logger.exception("Failed to initialize git repo in %s", repo_dir)
                raise
            try:
                await loop.run_in_executor(
                    None,
                    sandbox_backend.execute,
                    f"cd {repo_dir} && git remote remove origin",
                )
            except Exception:
                # Ignore if remote doesn't exist
                pass
            try:
                await loop.run_in_executor(
                    None,
                    sandbox_backend.execute,
                    f"cd {repo_dir} && git remote add origin {auth_url}",
                )
                await loop.run_in_executor(
                    None,
                    sandbox_backend.execute,
                    f"cd {repo_dir} && git fetch --prune origin",
                )
                checkout_result = await loop.run_in_executor(
                    None,
                    sandbox_backend.execute,
                    f"cd {repo_dir} && git checkout -B default origin/HEAD",
                )
                if checkout_result.exit_code != 0:
                    raise RuntimeError(
                        f"Failed to checkout origin/HEAD: {checkout_result.output}"
                    )
                await loop.run_in_executor(
                    None,
                    sandbox_backend.execute,
                    f"cd {repo_dir} && git remote set-url origin {clean_url}",
                )
            except Exception:
                logger.exception("Failed to re-initialize non-git repo directory")
                raise
        else:
            logger.info("Cloning repo %s/%s to %s", owner, repo, repo_dir)
            clone_url = f"https://git:{token}@github.com/{owner}/{repo}.git"
            try:
                result = await loop.run_in_executor(
                    None, sandbox_backend.execute, f"git clone {clone_url} {repo_dir}"
                )
                logger.debug("Git clone result: exit_code=%s", result.exit_code)
            except Exception:
                logger.exception("Failed to execute git clone")
                raise

            if result.exit_code != 0:
                msg = f"Failed to clone repo {owner}/{repo}: {result.output}"
                logger.error(msg)
                raise RuntimeError(msg)

            clean_url = f"https://github.com/{owner}/{repo}.git"
            try:
                await loop.run_in_executor(
                    None,
                    sandbox_backend.execute,
                    f"cd {repo_dir} && git remote set-url origin {clean_url}",
                )
            except Exception:
                logger.exception("Failed to set remote URL after clone")
                raise

    logger.info("Repo setup complete at %s", repo_dir)
    return repo_dir


async def _get_sandbox_id_from_metadata(thread_id: str) -> str | None:
    """Get sandbox_id from thread metadata."""
    thread = await client.threads.get(thread_id=thread_id)
    return thread.get("metadata", {}).get("sandbox_id")


async def _wait_for_sandbox_id(thread_id: str) -> str:
    """Wait for sandbox_id to be set in thread metadata.

    Polls thread metadata until sandbox_id is set to a real value
    (not the creating sentinel).

    Raises:
        TimeoutError: If sandbox creation takes too long
    """
    elapsed = 0.0
    while elapsed < SANDBOX_CREATION_TIMEOUT:
        sandbox_id = await _get_sandbox_id_from_metadata(thread_id)
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


DEFAULT_RECURSION_LIMIT = 1_000


async def get_agent(config: RunnableConfig) -> Pregel:  # noqa: PLR0915
    """Get or create an agent with a sandbox for the given thread."""
    thread_id = config["configurable"].get("thread_id", None)
    logger.info("get_agent called for thread %s", thread_id)

    config["recursion_limit"] = DEFAULT_RECURSION_LIMIT

    repo_config = config["configurable"].get("repo", {})
    repo_owner = repo_config.get("owner")
    repo_name = repo_config.get("name")

    encrypted_token = config["configurable"].get("github_token_encrypted")
    if encrypted_token:
        github_token = decrypt_token(encrypted_token)
        logger.debug("Decrypted GitHub token")

    if thread_id is None or not graph_loaded_for_execution(config):
        logger.info("No thread_id or not for execution, returning agent without sandbox")
        return create_deep_agent(
            system_prompt="",
            tools=[],
        ).with_config(config)

    sandbox_id = await _get_sandbox_id_from_metadata(thread_id)

    if sandbox_id == SANDBOX_CREATING:
        logger.info("Sandbox creation in progress, waiting...")
        sandbox_id = await _wait_for_sandbox_id(thread_id)

    if sandbox_id is None:
        logger.info("Creating new sandbox for thread %s", thread_id)
        await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": SANDBOX_CREATING})

        try:
            # Create sandbox without context manager cleanup (sandbox persists)
            sandbox_backend = await asyncio.to_thread(_create_langsmith_sandbox)
            logger.info("Sandbox created: %s", sandbox_backend.id)

            # Update metadata immediately after sandbox creation so other callers
            # can connect to the sandbox while we clone the repo
            await client.threads.update(
                thread_id=thread_id,
                metadata={"sandbox_id": sandbox_backend.id},
            )

            repo_dir = None
            if repo_owner and repo_name:
                logger.info("Cloning repo %s/%s into sandbox", repo_owner, repo_name)
                repo_dir = await _clone_or_pull_repo_in_sandbox(
                    sandbox_backend, repo_owner, repo_name, github_token
                )
                logger.info("Repo cloned to %s", repo_dir)

                await client.threads.update(
                    thread_id=thread_id,
                    metadata={"repo_dir": repo_dir},
                )
        except Exception:
            logger.exception("Failed to create sandbox or clone repo")
            try:
                await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": None})
                logger.info("Reset sandbox_id to None for thread %s", thread_id)
            except Exception:
                logger.exception("Failed to reset sandbox_id metadata")
            raise
    else:
        logger.info("Connecting to existing sandbox %s", sandbox_id)
        try:
            # Connect to existing sandbox without context manager cleanup
            sandbox_backend = await asyncio.to_thread(_create_langsmith_sandbox, sandbox_id)
            logger.info("Connected to existing sandbox %s", sandbox_id)
        except Exception:
            logger.warning("Failed to connect to existing sandbox %s, creating new one", sandbox_id)
            # Reset sandbox_id and create a new sandbox
            await client.threads.update(
                thread_id=thread_id,
                metadata={"sandbox_id": SANDBOX_CREATING},
            )

            try:
                sandbox_backend = await asyncio.to_thread(_create_langsmith_sandbox)
                logger.info("New sandbox created: %s", sandbox_backend.id)

                await client.threads.update(
                    thread_id=thread_id,
                    metadata={"sandbox_id": sandbox_backend.id},
                )
            except Exception:
                logger.exception("Failed to create replacement sandbox")
                await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": None})
                raise

        thread = await client.threads.get(thread_id=thread_id)
        repo_dir = thread.get("metadata", {}).get("repo_dir")

        if repo_owner and repo_name:
            logger.info("Pulling latest changes for repo %s/%s", repo_owner, repo_name)
            try:
                repo_dir = await _clone_or_pull_repo_in_sandbox(
                    sandbox_backend, repo_owner, repo_name, github_token
                )
            except Exception:
                logger.exception("Failed to pull repo in existing sandbox")
                raise

    SANDBOX_BACKENDS[thread_id] = sandbox_backend

    linear_issue = config["configurable"].get("linear_issue", {})
    linear_project_id = linear_issue.get("linear_project_id", "")
    linear_issue_number = linear_issue.get("linear_issue_number", "")

    logger.info("Returning agent with sandbox for thread %s", thread_id)
    return create_deep_agent(
        model=ChatAnthropic(model="claude-opus-4-6", max_tokens=20_000),
        system_prompt=construct_system_prompt(
            repo_dir,
            linear_project_id=linear_project_id,
            linear_issue_number=linear_issue_number,
        ),
        tools=[http_request, fetch_url, commit_and_open_pr],
        backend=sandbox_backend,
        middleware=[
            ToolErrorMiddleware(),
            check_message_queue_before_model,
            post_to_linear_after_model,
            open_pr_if_needed,
        ],
    ).with_config(config)
