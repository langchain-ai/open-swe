import logging
from importlib import resources
from pathlib import Path

from agent.config import ENV
from agent.prompts import prompt
from agent.utils.authorship import (
    OPEN_SWE_BOT_EMAIL,
    OPEN_SWE_BOT_NAME,
    PR_ATTRIBUTION_TEXT,
)

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_PATH = ENV.DEFAULT_PROMPT_PATH.optional()
EXTERNAL_UNTRUSTED_COMMENTS_SECTION = prompt("system/external-untrusted-comments")


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


def _render_source_guidance(
    source: str, slack_context: bool, slack_ask: bool = False, slack_breakout: bool = False
) -> str:
    if source == "background_task":
        name = "background-task"
    elif source == "slack" and slack_context:
        name = "slack-ask" if slack_ask else "slack"
    elif source in {"linear", "github", "schedule", "dashboard"}:
        name = source
    else:
        name = "generic"
    if name in {"slack", "schedule"}:
        guidance = prompt(f"system/source-{name}", breakout=slack_breakout, slack=slack_context)
    else:
        guidance = prompt(f"system/source-{name}")
    return f"<open_swe_source_context>\n{guidance}\n</open_swe_source_context>"


def _render_repository_scope_section() -> str:
    """Render the configured organization boundary for repository edits."""
    orgs = dict.fromkeys(
        org.strip().lower() for org in ENV.ALLOWED_GITHUB_ORGS.get().split(",") if org.strip()
    )
    if not orgs:
        return ""
    return prompt(
        "system/repository-scope",
        allowed_orgs=", ".join(f"`{org}`" for org in orgs),
    )


def _render_repo_instructions_section(instructions: str | None) -> str:
    if not instructions or not instructions.strip():
        return ""
    return prompt("system/repo-instructions", instructions=instructions.strip())


def _render_workspace_section(name: str | None, instructions: str | None) -> str:
    if not instructions or not instructions.strip():
        return ""
    label = f" ({name.strip()})" if name and name.strip() else ""
    return prompt(
        "system/workspace-instructions",
        label=label,
        instructions=instructions.strip(),
    )


def _render_collaboration_section() -> str:
    return prompt(
        "system/collaboration",
        bot_coauthor_trailer=f"Co-authored-by: {OPEN_SWE_BOT_NAME} <{OPEN_SWE_BOT_EMAIL}>",
        pr_attribution_text=PR_ATTRIBUTION_TEXT,
    )


def _working_environment_prompt(source: str, *, local_checkout: bool) -> str:
    if source == "desktop":
        return "system/working-environment-desktop"
    if local_checkout:
        return "system/working-environment-local"
    return "system/working-environment"


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
    sole_writer: bool = False,
    source: str = "dashboard",
    slack_context: bool = False,
    slack_ask: bool = False,
    slack_breakout: bool = False,
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
    return prompt(
        "system/main",
        working_dir=working_dir,
        local_checkout=local_checkout,
        desktop=source == "desktop",
        admin_workspaces=admin_workspaces,
        sole_writer=sole_writer,
        continued_from_collaborative=continued_from_collaborative,
        sandbox_file_downloads=sandbox_file_downloads,
        default_repo=(
            f"{default_repo['owner']}/{default_repo['name']}"
            if default_repo and default_repo.get("owner") and default_repo.get("name")
            else ""
        ),
        working_environment_section=prompt(
            _working_environment_prompt(source, local_checkout=local_checkout),
            working_dir=working_dir,
        ),
        dashboard_context_section=prompt(
            "system/dashboard-context",
            dashboard_base_url=dashboard_base_url or "(dashboard URL unavailable)",
            artifact_url=artifact_url or "(artifact link unavailable)",
        ),
        source_guidance_section=prompt(
            "system/source-context",
            source_guidance=_render_source_guidance(
                source, slack_context, slack_ask, slack_breakout
            ),
        ),
        default_prompt_section=_load_default_prompt(),
        repository_scope_section=(
            _render_repository_scope_section() if source in {"dashboard", "slack"} else ""
        ),
        collaboration_section=_render_collaboration_section(),
        external_untrusted_comments_section=EXTERNAL_UNTRUSTED_COMMENTS_SECTION,
        repo_instructions_section=_render_repo_instructions_section(repo_custom_instructions),
        recent_thread_context_section=recent_thread_context or "",
        workspace_section=_render_workspace_section(workspace_name, workspace_instructions),
    )
