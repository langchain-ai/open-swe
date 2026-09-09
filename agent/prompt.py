import logging
import shlex
from collections.abc import Sequence
from importlib import resources
from pathlib import Path

from agent.config import ENV
from agent.github.comments import UNTRUSTED_GITHUB_COMMENT_OPEN_TAG
from agent.prompts import load_prompt, render_prompt
from agent.utils.authorship import (
    OPEN_SWE_BOT_EMAIL,
    OPEN_SWE_BOT_NAME,
    CollaboratorIdentity,
    build_pr_attribution_footer,
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
    return f"{OPEN_SWE_SHARED_BASE}\n\n{load_prompt('system/sandbox-file-downloads.md')}"


def _render_source_guidance(source: str, slack_context: bool) -> str:
    if source == "background_task":
        name = "background-task"
    elif source == "slack" and slack_context:
        name = "slack"
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


def _render_collaboration_section(
    identity: CollaboratorIdentity | None,
    thread_url: str | None = None,
    model_id: str | None = None,
    reasoning_effort: str | None = None,
) -> str:
    if identity is None:
        return ""
    return render_prompt(
        "system/collaboration.md",
        display_name=identity.display_name,
        pr_attribution_footer=build_pr_attribution_footer(
            thread_url,
            model_id=model_id,
            reasoning_effort=reasoning_effort,
        ),
        bot_coauthor_trailer=f"Co-authored-by: {OPEN_SWE_BOT_NAME} <{OPEN_SWE_BOT_EMAIL}>",
    )


def _render_repo_instructions_section(instructions: str | None) -> str:
    if not instructions or not instructions.strip():
        return ""
    return render_prompt("system/repo-instructions.md", instructions=instructions.strip())


def _render_environment_section(name: str | None, instructions: str | None) -> str:
    if not instructions or not instructions.strip():
        return ""
    label = f" ({name.strip()})" if name and name.strip() else ""
    return render_prompt(
        "system/environment-instructions.md",
        label=label,
        instructions=instructions.strip(),
    )


def _render_user_instructions_section(instructions: str | None) -> str:
    if not instructions or not instructions.strip():
        return ""
    return render_prompt("system/user-instructions.md", instructions=instructions.strip())


def _git_identity_command(identity: CollaboratorIdentity) -> str:
    return (
        f"git config user.name {shlex.quote(identity.commit_name)} "
        f"&& git config user.email {shlex.quote(identity.commit_email)}"
    )


def _render_participant_identities(identities: Sequence[CollaboratorIdentity]) -> str:
    if not identities:
        return ""
    lines = "\n".join(
        f"- **{identity.display_name}**: `{_git_identity_command(identity)}`"
        for identity in identities
    )
    return f"Git identities you may author commits as:\n\n{lines}"


def construct_sender_context(
    identity: CollaboratorIdentity | None,
    *,
    user_custom_instructions: str | None = None,
    draft_prs: bool = True,
    thread_url: str | None = None,
    model_id: str | None = None,
    reasoning_effort: str | None = None,
    workspace_admin: bool = False,
    participant_identities: Sequence[CollaboratorIdentity] = (),
) -> str:
    resolved_identity = identity or CollaboratorIdentity(
        display_name=OPEN_SWE_BOT_NAME,
        commit_name=OPEN_SWE_BOT_NAME,
        commit_email=OPEN_SWE_BOT_EMAIL,
    )
    known = {other.commit_email for other in participant_identities}
    identities = [
        *([resolved_identity] if resolved_identity.commit_email not in known else []),
        *participant_identities,
    ]
    sections = [
        "This metadata was generated by Open SWE for the sender of this message. It applies "
        "only to this turn and must not be attributed to other thread participants.",
        f"Workspace admin: {'yes' if workspace_admin else 'no'}.",
        f"Sender's git identity command: `{_git_identity_command(resolved_identity)}`",
        _render_participant_identities(identities),
        _render_collaboration_section(
            resolved_identity,
            thread_url,
            model_id,
            reasoning_effort,
        ),
        f"New PRs are created {'as drafts' if draft_prs else 'ready for review'} for this sender.",
        _render_user_instructions_section(user_custom_instructions),
    ]
    return "\n\n".join(section for section in sections if section)


def construct_system_prompt(
    working_dir: str,
    dashboard_base_url: str = "",
    linear_project_id: str = "",
    linear_issue_number: str = "",
    default_repo: dict[str, str] | None = None,
    plan_mode: bool = False,
    plan_url: str | None = None,
    repo_custom_instructions: str | None = None,
    corridor_enabled: bool = False,
    environment_name: str | None = None,
    environment_instructions: str | None = None,
    admin_environments: bool = False,
    source: str = "dashboard",
    slack_context: bool = False,
    sandbox_file_downloads: bool = False,
) -> str:
    del linear_project_id, linear_issue_number
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
            "system/working-environment-desktop.md"
            if source == "desktop"
            else "system/working-environment.md",
            working_dir=working_dir,
        ),
        dashboard_context_section=render_prompt(
            "system/dashboard-context.md",
            dashboard_base_url=dashboard_base_url or "(dashboard URL unavailable)",
        ),
        source_guidance_section=render_prompt(
            "system/source-context.md",
            source_guidance=_render_source_guidance(source, slack_context),
        ),
        plan_mode_guidance_section=render_prompt(
            "system/plan-mode-guidance.md",
            plan_mode_entry_guidance=load_prompt("system/plan-mode-entry.md"),
            plan_review_url=plan_url or "(the dashboard plan-review page)",
        ),
        plan_mode_section=(
            render_prompt(
                "system/plan-mode-active.md",
                plan_url=plan_url or "(plan-review link unavailable)",
            )
            if plan_mode
            else ""
        ),
        self_awareness_section=load_prompt("system/self-awareness.md"),
        default_prompt_section=default_prompt_section,
        repository_scope_section=(
            _render_repository_scope_section() if source in {"dashboard", "slack"} else ""
        ),
        repository_setup_section=render_prompt(
            "system/repository-setup.md", working_dir=working_dir
        ),
        task_execution_section=load_prompt("system/task-execution.md"),
        corridor_prompt_section=load_prompt("system/corridor.md") if corridor_enabled else "",
        dependency_section=load_prompt("system/dependencies.md"),
        external_untrusted_comments_section=EXTERNAL_UNTRUSTED_COMMENTS_SECTION,
        commit_pr_section=commit_pr_section,
        repo_instructions_section=_render_repo_instructions_section(repo_custom_instructions),
        environment_section=_render_environment_section(environment_name, environment_instructions),
        admin_environment_section=(
            load_prompt("system/admin-environment.md") if admin_environments else ""
        ),
        shared_base_section=(
            "- If a user asks to change the managed workspace environment, direct them to start "
            "an admin thread in the Web UI and require them to be a workspace admin. Admin threads "
            "cannot be started from Slack or with agent thread tools.\n\n"
            if not admin_environments
            else ""
        )
        + render_open_swe_shared_base(sandbox_file_downloads=sandbox_file_downloads),
    )
