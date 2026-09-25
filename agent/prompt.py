import logging
from importlib import resources
from pathlib import Path

from agent.config import ENV
from agent.github.comments import UNTRUSTED_GITHUB_COMMENT_OPEN_TAG
from agent.prompts import load_prompt, render_prompt
from agent.utils.authorship import (
    OPEN_SWE_BOT_EMAIL,
    OPEN_SWE_BOT_NAME,
    PR_ATTRIBUTION_TEXT,
)

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_PATH = ENV.DEFAULT_PROMPT_PATH.optional()
OPEN_SWE_SHARED_BASE = load_prompt("system/shared-base.md")
EXTERNAL_UNTRUSTED_COMMENTS_SECTION = render_prompt(
    "system/external-untrusted-comments.md",
    untrusted_comment_open_tag=UNTRUSTED_GITHUB_COMMENT_OPEN_TAG,
)


def _load_default_prompt() -> str:
    """Load the configured default prompt."""
    try:
        if DEFAULT_PROMPT_PATH:
            content = Path(DEFAULT_PROMPT_PATH).read_text().strip()
        else:
            content = (
                resources.files("agent.resources")
                .joinpath("default_prompt.md")
                .read_text(encoding="utf-8")
                .strip()
            )
        if content:
            return f"---\n\n### Custom Instructions\n\n{content}"
    except Exception:
        logger.warning(
            "Failed to read default prompt from %s",
            DEFAULT_PROMPT_PATH or "agent.resources/default_prompt.md",
        )
    return ""


def render_open_swe_shared_base(*, sandbox_file_downloads: bool) -> str:
    """Render shared guidance for the tools available to this agent."""
    if not sandbox_file_downloads:
        return OPEN_SWE_SHARED_BASE
    return (
        f"{OPEN_SWE_SHARED_BASE}\n\n"
        f"{load_prompt('system/sandbox-file-downloads.md')}\n\n"
        f"{load_prompt('system/sandbox-browser-capture.md')}"
    )


def _render_source_guidance(source: str, slack_context: bool, slack_ask: bool = False) -> str:
    if source == "background_task":
        name = "background-task"
    elif source == "slack" and slack_context:
        name = "slack-ask" if slack_ask else "slack"
    elif source == "linear":
        name = "linear"
    elif source == "github":
        name = "github"
    elif source == "schedule":
        name = "schedule-slack" if slack_context else "schedule"
    elif source == "dashboard":
        name = "dashboard"
    else:
        name = "generic"
    guidance = load_prompt(f"system/source-{name}.md")
    return f"<open_swe_source_context>\n{guidance}\n</open_swe_source_context>"


def _render_repository_scope_section() -> str:
    """Render the configured organization boundary for repository edits."""
    orgs = dict.fromkeys(
        org.strip().lower() for org in ENV.ALLOWED_GITHUB_ORGS.get().split(",") if org.strip()
    )
    if not orgs:
        return ""
    return render_prompt(
        "system/repository-scope.md",
        allowed_orgs=", ".join(f"`{org}`" for org in orgs),
    )


def _render_repo_instructions_section(instructions: str | None) -> str:
    if not instructions or not instructions.strip():
        return ""
    return render_prompt("system/repo-instructions.md", instructions=instructions.strip())


def _render_workspace_section(name: str | None, instructions: str | None) -> str:
    if not instructions or not instructions.strip():
        return ""
    label = f" ({name.strip()})" if name and name.strip() else ""
    return render_prompt(
        "system/workspace-instructions.md",
        label=label,
        instructions=instructions.strip(),
    )


def _render_collaboration_section() -> str:
    return render_prompt(
        "system/collaboration.md",
        bot_coauthor_trailer=f"Co-authored-by: {OPEN_SWE_BOT_NAME} <{OPEN_SWE_BOT_EMAIL}>",
        pr_attribution_text=PR_ATTRIBUTION_TEXT,
    )


def _working_environment_prompt(source: str, *, local_checkout: bool) -> str:
    if source == "desktop":
        return "system/working-environment-desktop.md"
    if local_checkout:
        return "system/working-environment-local.md"
    return "system/working-environment.md"


def construct_system_prompt(
    working_dir: str,
    dashboard_base_url: str = "",
    artifact_url: str | None = None,
    linear_project_id: str = "",
    linear_issue_number: str = "",
    default_repo: dict[str, str] | None = None,
    repo_custom_instructions: str | None = None,
    workspace_name: str | None = None,
    workspace_instructions: str | None = None,
    admin_workspaces: bool = False,
    source: str = "dashboard",
    slack_context: bool = False,
    slack_ask: bool = False,
    sandbox_file_downloads: bool = False,
    continued_from_collaborative: bool = False,
    local_checkout: bool = False,
    recent_thread_context: str | None = None,
) -> str:
    """Render the agent's system prompt.

    ``local_checkout`` says the working directory already *is* the user's own
    repository — a thread bridged to their machine — so the clone-or-sync and
    git-identity steps a hosted sandbox needs would rewrite their checkout.
    """
    del linear_project_id, linear_issue_number
    untrusted_section = EXTERNAL_UNTRUSTED_COMMENTS_SECTION
    if continued_from_collaborative:
        untrusted_section += f"\n\n{load_prompt('system/continued-from-collaborative.md')}"
    default_prompt_section = _load_default_prompt()
    if default_repo and default_repo.get("owner") and default_repo.get("name"):
        repo_line = (
            "When a repository is not explicitly mentioned, use "
            f"`{default_repo['owner']}/{default_repo['name']}`."
        )
        default_prompt_section += f"\n\n{repo_line}"
    commit_pr_section = load_prompt("system/commit-pr.md")
    if source == "desktop":
        commit_pr_section += f"\n\n{load_prompt('system/commit-pr-desktop.md')}"
    return render_prompt(
        "system/main.md",
        working_environment_section=render_prompt(
            _working_environment_prompt(source, local_checkout=local_checkout),
            working_dir=working_dir,
        ),
        dashboard_context_section=render_prompt(
            "system/dashboard-context.md",
            dashboard_base_url=dashboard_base_url or "(dashboard URL unavailable)",
            artifact_url=artifact_url or "(artifact link unavailable)",
        ),
        source_guidance_section=render_prompt(
            "system/source-context.md",
            source_guidance=_render_source_guidance(source, slack_context, slack_ask),
        ),
        self_awareness_section=load_prompt("system/self-awareness.md"),
        default_prompt_section=default_prompt_section,
        repository_scope_section=(
            _render_repository_scope_section() if source in {"dashboard", "slack"} else ""
        ),
        repository_setup_section=render_prompt(
            "system/repository-setup-local.md" if local_checkout else "system/repository-setup.md",
            working_dir=working_dir,
        ),
        collaboration_section=_render_collaboration_section(),
        task_execution_section=load_prompt("system/task-execution.md"),
        dependency_section=load_prompt("system/dependencies.md"),
        external_untrusted_comments_section=untrusted_section,
        commit_pr_section=commit_pr_section,
        repo_instructions_section=_render_repo_instructions_section(repo_custom_instructions),
        recent_thread_context_section=recent_thread_context or "",
        workspace_section=_render_workspace_section(workspace_name, workspace_instructions),
        admin_workspace_section=(
            load_prompt("system/admin-workspace.md") if admin_workspaces else ""
        ),
        shared_base_section=(
            load_prompt("system/workspace-admin-required.md") + "\n\n"
            if not admin_workspaces
            else ""
        )
        + render_open_swe_shared_base(sandbox_file_downloads=sandbox_file_downloads),
    )
