"""Main entry point and CLI loop for Open SWE agent."""
# ruff: noqa: E402

# Suppress deprecation warnings from langchain_core (e.g., Pydantic V1 on Python 3.14+)
# ruff: noqa: E402
import logging
import os
import shlex
import warnings

logger = logging.getLogger(__name__)


def _is_local_poc_mode() -> bool:
    return os.getenv("SANDBOX_TYPE") == "local" and not any(
        os.getenv(key)
        for key in (
            "LANGSMITH_API_KEY",
            "LANGSMITH_API_KEY_PROD",
            "LANGSMITH_TENANT_ID_PROD",
            "LANGSMITH_TRACING_PROJECT_ID_PROD",
        )
    )


class _SuppressMessageFilter(logging.Filter):
    def __init__(self, *substrings: str):
        super().__init__()
        self.substrings = substrings

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not all(substring in message for substring in self.substrings)


if _is_local_poc_mode():
    warnings.filterwarnings(
        "ignore",
        message=r".*langsmith\.sandbox is in alpha.*",
        category=FutureWarning,
    )
    warnings.filterwarnings(
        "ignore",
        message=r".*Calling \.text\(\) as a method is deprecated.*",
    )
    warnings.filterwarnings(
        "ignore",
        message=r".*Pydantic serializer warnings:.*",
    )
    logging.getLogger("langsmith.client").addFilter(
        _SuppressMessageFilter("Failed to send compressed multipart ingest:")
    )
    logging.getLogger("langgraph_api.timing.timer").addFilter(
        _SuppressMessageFilter("Import for graph agent exceeded the expected startup time")
    )
    logging.getLogger("langgraph_api.server").addFilter(
        _SuppressMessageFilter("GET /threads/", "404")
    )
    logging.getLogger("httpx").addFilter(
        _SuppressMessageFilter(
            "GET http://localhost:2024/threads/",
            "404 Not Found",
        )
    )
    logging.getLogger("langgraph_api.graph").addFilter(
        _SuppressMessageFilter("Slow graph load. Accessing graph 'agent'")
    )
    logging.getLogger("watchfiles.main").setLevel(logging.WARNING)

from langgraph.config import get_config
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

from .utils.agents_md import read_agents_md_in_sandbox
from .utils.github import (
    _CRED_FILE_PATH,
    cleanup_git_credentials,
    get_github_default_branch,
    git_has_uncommitted_changes,
    is_valid_git_repo,
    remove_directory,
    setup_git_credentials,
)
from .utils.sandbox_paths import aresolve_repo_dir, aresolve_sandbox_work_dir
from .utils.sandbox_state import SANDBOX_BACKENDS, get_sandbox_id_from_metadata


def _get_reply_tools(source: str) -> list:
    """Return source-appropriate reply tools."""
    if source == "github":
        return [github_comment]
    if source == "slack":
        return [slack_thread_reply]
    if source == "linear":
        return [linear_comment]
    return [linear_comment, slack_thread_reply, github_comment]


async def _persist_sandbox_metadata(
    thread_id: str, sandbox_id: str, repo_dir: str | None = None
) -> None:
    """Store the active sandbox metadata on the thread."""
    metadata: dict[str, str] = {"sandbox_id": sandbox_id}
    if repo_dir:
        metadata["repo_dir"] = repo_dir
    await client.threads.update(thread_id=thread_id, metadata=metadata)


async def _clone_or_pull_repo_in_sandbox(  # noqa: PLR0915
    sandbox_backend: SandboxBackendProtocol,
    owner: str,
    repo: str,
    github_token: str | None = None,
    branch_name: str | None = None,
) -> str:
    """Clone a GitHub repo into the sandbox, or pull if it already exists.

    Args:
        sandbox_backend: The sandbox backend to execute commands in (LangSmithBackend)
        owner: GitHub repo owner
        repo: GitHub repo name
        github_token: GitHub access token (from agent auth or env var)
        branch_name: Optional existing branch to sync to instead of the default branch

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

    work_dir = await aresolve_sandbox_work_dir(sandbox_backend)
    repo_dir = await aresolve_repo_dir(sandbox_backend, repo)
    clean_url = f"https://github.com/{owner}/{repo}.git"
    cred_helper_arg = f"-c credential.helper='store --file={_CRED_FILE_PATH}'"
    safe_repo_dir = shlex.quote(repo_dir)
    safe_clean_url = shlex.quote(clean_url)

    logger.info("Resolved sandbox work dir to %s", work_dir)

    is_git_repo = await loop.run_in_executor(None, is_valid_git_repo, sandbox_backend, repo_dir)

    if not is_git_repo:
        logger.warning("Repo directory missing or not a valid git repo at %s, removing", repo_dir)
        try:
            removed = await loop.run_in_executor(None, remove_directory, sandbox_backend, repo_dir)
            if not removed:
                msg = f"Failed to remove invalid directory at {repo_dir}"
                logger.error(msg)
                raise RuntimeError(msg)
            logger.info("Removed invalid directory, will clone fresh repo")
        except Exception:
            logger.exception("Failed to remove invalid directory")
            raise
    else:
        logger.info("Repo exists at %s, checking for uncommitted changes", repo_dir)
        has_changes = await loop.run_in_executor(
            None, git_has_uncommitted_changes, sandbox_backend, repo_dir
        )

        if has_changes:
            logger.warning("Repo has uncommitted changes at %s, skipping pull", repo_dir)
            return repo_dir

        target_branch = branch_name or await get_github_default_branch(owner, repo, token)
        safe_target_branch = shlex.quote(target_branch)
        safe_origin_target_branch = shlex.quote(f"origin/{target_branch}")

        logger.info(
            "Repo is clean, syncing %s/%s to origin/%s",
            owner,
            repo,
            target_branch,
        )

        await loop.run_in_executor(None, setup_git_credentials, sandbox_backend, token)
        try:
            sync_result = await loop.run_in_executor(
                None,
                sandbox_backend.execute,
                " && ".join(
                    [
                        f"cd {repo_dir}",
                        f"git {cred_helper_arg} fetch origin --prune",
                        f"(git checkout {safe_target_branch} || git checkout -B {safe_target_branch} {safe_origin_target_branch})",
                        f"git reset --hard {safe_origin_target_branch}",
                        "git clean -fd",
                    ]
                ),
            )
            logger.debug("Git sync result: exit_code=%s", sync_result.exit_code)
            if sync_result.exit_code != 0:
                msg = (
                    f"Failed to sync repo {owner}/{repo} to origin/{target_branch}: "
                    f"{sync_result.output}"
                )
                logger.error(msg)
                raise RuntimeError(msg)
        except Exception:
            logger.exception("Failed to sync repo to the expected base branch")
            raise
        finally:
            await loop.run_in_executor(None, cleanup_git_credentials, sandbox_backend)

        logger.info("Repo updated at %s", repo_dir)
        return repo_dir

    logger.info("Cloning repo %s/%s to %s", owner, repo, repo_dir)
    await loop.run_in_executor(None, setup_git_credentials, sandbox_backend, token)
    try:
        result = await loop.run_in_executor(
            None,
            sandbox_backend.execute,
            f"git {cred_helper_arg} clone {safe_clean_url} {safe_repo_dir}",
        )
        logger.debug("Git clone result: exit_code=%s", result.exit_code)
    except Exception:
        logger.exception("Failed to execute git clone")
        raise
    finally:
        await loop.run_in_executor(None, cleanup_git_credentials, sandbox_backend)

    if result.exit_code != 0:
        msg = f"Failed to clone repo {owner}/{repo}: {result.output}"
        logger.error(msg)
        raise RuntimeError(msg)

    logger.info("Repo cloned successfully at %s", repo_dir)
    return repo_dir


async def _recreate_sandbox(
    thread_id: str,
    repo_owner: str,
    repo_name: str,
    *,
    github_token: str | None,
    branch_name: str | None = None,
) -> tuple[SandboxBackendProtocol, str]:
    """Recreate a sandbox and clone the repo after a connection failure.

    Clears the stale cache entry, sets the SANDBOX_CREATING sentinel,
    creates a fresh sandbox, and clones the repo.
    """
    SANDBOX_BACKENDS.pop(thread_id, None)
    await client.threads.update(
        thread_id=thread_id,
        metadata={"sandbox_id": SANDBOX_CREATING},
    )
    try:
        sandbox_backend = await asyncio.to_thread(create_sandbox)
        repo_dir = await _clone_or_pull_repo_in_sandbox(
            sandbox_backend, repo_owner, repo_name, github_token, branch_name
        )
        await _persist_sandbox_metadata(thread_id, sandbox_backend.id, repo_dir)
    except Exception:
        logger.exception("Failed to recreate sandbox after connection failure")
        await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": None})
        raise
    return sandbox_backend, repo_dir


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


async def get_agent(config: RunnableConfig) -> Pregel:  # noqa: PLR0915
    """Get or create an agent with a sandbox for the given thread."""
    thread_id = config["configurable"].get("thread_id", None)
    branch_name = config.get("metadata", {}).get("branch_name")

    config["recursion_limit"] = DEFAULT_RECURSION_LIMIT

    repo_config = config["configurable"].get("repo", {})
    repo_owner = repo_config.get("owner")
    repo_name = repo_config.get("name")

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
        metadata = get_config().get("metadata", {})
        repo_dir = metadata.get("repo_dir")

        if repo_owner and repo_name:
            logger.info("Pulling latest changes for repo %s/%s", repo_owner, repo_name)
            try:
                repo_dir = await _clone_or_pull_repo_in_sandbox(
                    sandbox_backend,
                    repo_owner,
                    repo_name,
                    github_token,
                    branch_name,
                )
            except SandboxClientError:
                logger.warning(
                    "Cached sandbox is no longer reachable for thread %s, recreating sandbox",
                    thread_id,
                )
                sandbox_backend, repo_dir = await _recreate_sandbox(
                    thread_id,
                    repo_owner,
                    repo_name,
                    github_token=github_token,
                    branch_name=branch_name,
                )
            except Exception:
                logger.exception("Failed to pull repo in cached sandbox")
                raise

    elif sandbox_id is None:
        logger.info("Creating new sandbox for thread %s", thread_id)
        await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": SANDBOX_CREATING})

        try:
            # Create sandbox without context manager cleanup (sandbox persists)
            sandbox_backend = await asyncio.to_thread(create_sandbox)
            logger.info("Sandbox created: %s", sandbox_backend.id)

            repo_dir = None
            if repo_owner and repo_name:
                logger.info("Cloning repo %s/%s into sandbox", repo_owner, repo_name)
                repo_dir = await _clone_or_pull_repo_in_sandbox(
                    sandbox_backend,
                    repo_owner,
                    repo_name,
                    github_token,
                    branch_name,
                )
                logger.info("Repo cloned to %s", repo_dir)
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
            sandbox_backend = await asyncio.to_thread(create_sandbox, sandbox_id)
            logger.info("Connected to existing sandbox %s", sandbox_id)
        except Exception:
            logger.warning("Failed to connect to existing sandbox %s, creating new one", sandbox_id)
            # Reset sandbox_id and create a new sandbox
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

        metadata = get_config().get("metadata", {})
        repo_dir = metadata.get("repo_dir")

        if repo_owner and repo_name:
            logger.info("Pulling latest changes for repo %s/%s", repo_owner, repo_name)
            try:
                repo_dir = await _clone_or_pull_repo_in_sandbox(
                    sandbox_backend,
                    repo_owner,
                    repo_name,
                    github_token,
                    branch_name,
                )
            except SandboxClientError:
                logger.warning(
                    "Existing sandbox is no longer reachable for thread %s, recreating sandbox",
                    thread_id,
                )
                sandbox_backend, repo_dir = await _recreate_sandbox(
                    thread_id,
                    repo_owner,
                    repo_name,
                    github_token=github_token,
                    branch_name=branch_name,
                )
            except Exception:
                logger.exception("Failed to pull repo in existing sandbox")
                raise

    SANDBOX_BACKENDS[thread_id] = sandbox_backend
    await _persist_sandbox_metadata(thread_id, sandbox_backend.id, repo_dir)

    if not repo_dir:
        msg = "Cannot proceed: no repo was cloned. Set 'repo.owner' and 'repo.name' in the configurable config"
        raise RuntimeError(msg)

    if branch_name:
        logger.info("Checking out branch '%s' in sandbox for thread %s", branch_name, thread_id)
        loop = asyncio.get_event_loop()
        safe_repo_dir = shlex.quote(repo_dir)
        safe_branch = shlex.quote(branch_name)
        checkout_result = await loop.run_in_executor(
            None,
            sandbox_backend.execute,
            f"cd {safe_repo_dir} && git fetch origin && git checkout {safe_branch}",
        )
        if checkout_result.exit_code != 0:
            logger.warning(
                "Failed to checkout branch '%s': %s",
                branch_name,
                checkout_result.output[:200] if checkout_result.output else "",
            )

    linear_issue = config["configurable"].get("linear_issue", {})
    linear_project_id = linear_issue.get("linear_project_id", "")
    linear_issue_number = linear_issue.get("linear_issue_number", "")
    source = config["configurable"].get("source", "")
    agents_md = await read_agents_md_in_sandbox(sandbox_backend, repo_dir)

    logger.info("Returning agent with sandbox for thread %s", thread_id)
    return create_deep_agent(
        model=make_model(
            os.environ.get("LLM_MODEL_ID", DEFAULT_LLM_MODEL_ID),
            temperature=0,
            max_tokens=20_000,
        ),
        system_prompt=construct_system_prompt(
            repo_dir,
            linear_project_id=linear_project_id,
            linear_issue_number=linear_issue_number,
            agents_md=agents_md,
        ),
        tools=[
            http_request,
            fetch_url,
            web_search,
            commit_and_open_pr,
            linear_create_issue,
            linear_delete_issue,
            linear_get_issue,
            linear_get_issue_comments,
            linear_list_teams,
            linear_update_issue,
            list_pr_reviews,
            get_pr_review,
            create_pr_review,
            update_pr_review,
            dismiss_pr_review,
            submit_pr_review,
            list_pr_review_comments,
            *_get_reply_tools(source),
        ],
        backend=sandbox_backend,
        middleware=[
            ToolErrorMiddleware(),
            check_message_queue_before_model,
            ensure_no_empty_msg,
            open_pr_if_needed,
        ],
    ).with_config(config)
