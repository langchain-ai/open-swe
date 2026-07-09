"""Clone or pull Azure DevOps Git repos into a sandbox using a PAT."""

from __future__ import annotations

import asyncio
import logging
import shlex

from deepagents.backends.protocol import SandboxBackendProtocol

from .azure_devops import azure_devops_https_clone_url
from .sandbox_git import (
    execute_sandbox_git,
    git_command_failure_hints,
    sandbox_git_clone_depth_args,
)
from .sandbox_paths import aresolve_repo_dir
from .scm_git import (
    azure_devops_git_c_http_extra_header,
    git_has_uncommitted_changes,
    is_valid_git_repo,
    remove_directory,
    validate_git_branch_short_name,
)

logger = logging.getLogger(__name__)


async def checkout_azure_devops_branch_in_sandbox(
    sandbox_backend: SandboxBackendProtocol,
    repo_dir: str,
    branch_short_name: str,
    pat: str,
    *,
    git_auth_via_proxy: bool = False,
) -> None:
    branch = validate_git_branch_short_name(branch_short_name)
    ado_c_arg = "" if git_auth_via_proxy else f"-c {azure_devops_git_c_http_extra_header(pat)} "
    safe_repo = shlex.quote(repo_dir)
    safe_branch = shlex.quote(branch)
    safe_ref = shlex.quote(f"refs/remotes/origin/{branch}")
    refspec = f"+refs/heads/{branch}:refs/remotes/origin/{branch}"
    cmd = (
        f"cd {safe_repo} && git {ado_c_arg}fetch origin {shlex.quote(refspec)} "
        f"&& git {ado_c_arg}checkout -B {safe_branch} {safe_ref}"
    )
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, execute_sandbox_git, sandbox_backend, cmd)
    if result.exit_code != 0:
        logger.warning(
            "Azure DevOps checkout branch %r failed (exit=%s): %s",
            branch,
            result.exit_code,
            (result.output or "")[:500],
        )


async def clone_or_pull_azure_devops_repo_in_sandbox(
    sandbox_backend: SandboxBackendProtocol,
    organization: str,
    project: str,
    repository_name: str,
    pat: str,
    *,
    checkout_branch: str | None = None,
    git_auth_via_proxy: bool = False,
) -> str:
    """Clone or pull an Azure DevOps Git repo over HTTPS using a PAT."""
    if not pat:
        raise ValueError("No Azure DevOps PAT provided")

    loop = asyncio.get_running_loop()
    repo_dir = await aresolve_repo_dir(sandbox_backend, repository_name)
    clean_url = azure_devops_https_clone_url(organization, project, repository_name)
    ado_c_prefix = "" if git_auth_via_proxy else f"-c {azure_devops_git_c_http_extra_header(pat)} "
    safe_repo_dir = shlex.quote(repo_dir)
    safe_clean_url = shlex.quote(clean_url)

    is_git_repo = await loop.run_in_executor(None, is_valid_git_repo, sandbox_backend, repo_dir)
    if is_git_repo:
        has_changes = await loop.run_in_executor(
            None, git_has_uncommitted_changes, sandbox_backend, repo_dir
        )
        if has_changes:
            logger.warning("Azure DevOps repo has uncommitted changes at %s, skipping pull", repo_dir)
        else:
            pull_cmd = (
                f"cd {safe_repo_dir} && git {ado_c_prefix}pull origin "
                "$(git rev-parse --abbrev-ref HEAD)"
            )
            pull_result = await loop.run_in_executor(
                None, execute_sandbox_git, sandbox_backend, pull_cmd
            )
            if pull_result.exit_code != 0:
                logger.warning("Azure DevOps git pull failed: %s", (pull_result.output or "")[:500])
    else:
        await loop.run_in_executor(None, remove_directory, sandbox_backend, repo_dir)
        depth_args = sandbox_git_clone_depth_args()
        clone_cmd = f"git {ado_c_prefix}clone{depth_args} {safe_clean_url} {safe_repo_dir}"
        result = await loop.run_in_executor(None, execute_sandbox_git, sandbox_backend, clone_cmd)
        if result.exit_code != 0:
            hint = git_command_failure_hints(
                git_output=result.output or "",
                is_azure_devops=True,
            )
            msg = f"Failed to clone Azure DevOps repo: {result.output}{hint}"
            logger.error(msg)
            raise RuntimeError(msg)

    if checkout_branch and checkout_branch.strip():
        await checkout_azure_devops_branch_in_sandbox(
            sandbox_backend,
            repo_dir,
            checkout_branch.strip(),
            pat,
            git_auth_via_proxy=git_auth_via_proxy,
        )

    logger.info(
        "Azure DevOps repo ready at %s (org=%s project=%s repo=%s)",
        repo_dir,
        organization,
        project,
        repository_name,
    )
    return repo_dir
