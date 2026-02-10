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


def _get_langsmith_api_key() -> str | None:
    """Get LangSmith API key from environment.

    Checks LANGSMITH_API_KEY first, then falls back to LANGSMITH_API_KEY_PROD
    for LangGraph Cloud deployments where LANGSMITH_API_KEY is reserved.
    """
    return os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGSMITH_API_KEY_PROD")


def _get_sandbox_template_config() -> tuple[str | None, str | None]:
    """Get sandbox template configuration from environment.

    Returns:
        Tuple of (template_name, template_image) from environment variables.
        Values are None if not set in environment.
    """
    template_name = os.environ.get("DEFAULT_SANDBOX_TEMPLATE_NAME")
    template_image = os.environ.get("DEFAULT_SANDBOX_TEMPLATE_IMAGE")
    return template_name, template_image


def _create_langsmith_sandbox(
    sandbox_id: str | None = None,
) -> SandboxBackendProtocol:
    """Create or connect to a LangSmith sandbox without automatic cleanup.

    This function directly uses the LangSmithProvider to create/connect to sandboxes
    without the context manager cleanup, allowing sandboxes to persist across
    multiple agent invocations.

    Args:
        sandbox_id: Optional existing sandbox ID to connect to.
                   If None, creates a new sandbox.

    Returns:
        SandboxBackendProtocol instance
    """
    from .integrations.langsmith import LangSmithProvider

    api_key = _get_langsmith_api_key()
    template_name, template_image = _get_sandbox_template_config()

    provider = LangSmithProvider(api_key=api_key)
    return provider.get_or_create(
        sandbox_id=sandbox_id,
        template=template_name,
        template_image=template_image,
    )


client = get_client()

SANDBOX_CREATING = "__creating__"
SANDBOX_CREATION_TIMEOUT = 180
SANDBOX_POLL_INTERVAL = 1.0

# HTTP status codes
HTTP_CREATED = 201
HTTP_UNPROCESSABLE_ENTITY = 422

# Message count thresholds
_SANDBOX_BACKENDS: dict[str, Any] = {}

import httpx

LINEAR_API_KEY = os.environ.get("LINEAR_API_KEY", "")


async def create_github_pr(
    repo_owner: str,
    repo_name: str,
    github_token: str,
    title: str,
    head_branch: str,
    base_branch: str,
    body: str,
) -> tuple[str | None, int | None]:
    """Create a GitHub pull request via the API.

    Args:
        repo_owner: Repository owner (e.g., "langchain-ai")
        repo_name: Repository name (e.g., "deepagents")
        github_token: GitHub access token
        title: PR title
        head_branch: Source branch name
        base_branch: Target branch name
        body: PR description

    Returns:
        Tuple of (pr_url, pr_number) if successful, (None, None) otherwise
    """
    pr_payload = {
        "title": title,
        "head": head_branch,
        "base": base_branch,
        "body": body,
    }

    logger.info(
        "Creating PR: head=%s, base=%s, repo=%s/%s",
        head_branch,
        base_branch,
        repo_owner,
        repo_name,
    )

    try:
        async with httpx.AsyncClient() as http_client:
            pr_response = await http_client.post(
                f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls",
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json=pr_payload,
            )

            pr_data = pr_response.json()

            if pr_response.status_code == HTTP_CREATED:
                pr_url = pr_data.get("html_url")
                pr_number = pr_data.get("number")
                logger.info("PR created successfully: %s", pr_url)
                return pr_url, pr_number

            if pr_response.status_code == HTTP_UNPROCESSABLE_ENTITY:
                logger.error("GitHub API validation error (422): %s", pr_data.get("message"))
            else:
                logger.error(
                    "GitHub API error (%s): %s",
                    pr_response.status_code,
                    pr_data.get("message"),
                )

            if "errors" in pr_data:
                logger.error("GitHub API errors detail: %s", pr_data.get("errors"))

            return None, None

    except httpx.HTTPError:
        logger.exception("Failed to create PR via GitHub API")
        return None, None


async def get_github_default_branch(
    repo_owner: str,
    repo_name: str,
    github_token: str,
) -> str:
    """Get the default branch of a GitHub repository via the API.

    Args:
        repo_owner: Repository owner (e.g., "langchain-ai")
        repo_name: Repository name (e.g., "deepagents")
        github_token: GitHub access token

    Returns:
        The default branch name (e.g., "main" or "master")
    """
    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(
                f"https://api.github.com/repos/{repo_owner}/{repo_name}",
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )

            if response.status_code == 200:  # noqa: PLR2004
                repo_data = response.json()
                default_branch = repo_data.get("default_branch", "main")
                logger.debug("Got default branch from GitHub API: %s", default_branch)
                return default_branch

            logger.warning(
                "Failed to get repo info from GitHub API (%s), falling back to 'main'",
                response.status_code,
            )
            return "main"

    except httpx.HTTPError:
        logger.exception("Failed to get default branch from GitHub API, falling back to 'main'")
        return "main"


async def comment_on_linear_issue(issue_id: str, comment_body: str) -> bool:
    """Add a comment to a Linear issue.

    Args:
        issue_id: The Linear issue ID
        comment_body: The comment text

    Returns:
        True if successful, False otherwise
    """
    if not LINEAR_API_KEY:
        return False

    import httpx

    url = "https://api.linear.app/graphql"

    mutation = """
    mutation CommentCreate($issueId: String!, $body: String!) {
        commentCreate(input: { issueId: $issueId, body: $body }) {
            success
            comment {
                id
            }
        }
    }
    """

    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.post(
                url,
                headers={
                    "Authorization": LINEAR_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "query": mutation,
                    "variables": {"issueId": issue_id, "body": comment_body},
                },
            )
            response.raise_for_status()
            result = response.json()
            return bool(result.get("data", {}).get("commentCreate", {}).get("success"))
        except Exception:  # noqa: BLE001
            return False


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
        check_result = await loop.run_in_executor(
            None, sandbox_backend.execute, f"test -d {repo_dir}/.git && echo exists"
        )
        logger.debug(
            "Check result: exit_code=%s, output=%s",
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

    _SANDBOX_BACKENDS[thread_id] = sandbox_backend

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
