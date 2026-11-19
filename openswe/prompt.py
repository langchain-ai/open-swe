import logging
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from runtime_api.provenance import Provenance

from openswe.bridge.constants import BridgeClient
from openswe.config import ENV
from openswe.input_messages import RunMessage, dynamic_context_hash, run_context
from openswe.prompts import prompt
from openswe.utils.authorship import (
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
                resources.files("openswe.resources")
                .joinpath("default_prompt.md")
                .read_text(encoding="utf-8")
                .strip()
            )
        if content:
            return f"---\n\n### Custom Instructions\n\n{content}"
    except Exception:
        logger.warning(
            "Failed to read default prompt from %s",
            DEFAULT_PROMPT_PATH or "openswe.resources/default_prompt.md",
        )
    return ""


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


def _render_workspace_section(
    name: str | None, instructions: str | None, repos: list[str] | None
) -> str:
    if not (instructions and instructions.strip()) and not repos:
        return ""
    label = f" ({name.strip()})" if name and name.strip() else ""
    return prompt(
        "system/workspace-instructions",
        label=label,
        instructions=(instructions or "").strip(),
        repos=repos or [],
    )


def _render_collaboration_section() -> str:
    return prompt(
        "system/collaboration",
        bot_coauthor_trailer=f"Co-authored-by: {OPEN_SWE_BOT_NAME} <{OPEN_SWE_BOT_EMAIL}>",
        pr_attribution_text=PR_ATTRIBUTION_TEXT,
    )


@dataclass(frozen=True)
class SystemPrompt:
    """Cacheable shared, execution/workspace and thread prompt components."""

    shared: str
    workspace: str = ""
    thread: str = ""

    @staticmethod
    def execution_section(_name: str | None, instructions: str, repos: list[str]) -> str:
        if not instructions.strip() and not repos:
            return ""
        return prompt(
            "system/execution-instructions", instructions=instructions.strip(), repos=repos
        )

    @classmethod
    def from_state(cls, state: Mapping[str, object]) -> SystemPrompt | None:
        full = state.get("rendered_system_prompt")
        workspace = state.get("rendered_workspace_prompt")
        thread = state.get("rendered_thread_prompt")
        if not all(isinstance(part, str) for part in (full, workspace, thread)):
            return None
        assert isinstance(full, str) and isinstance(workspace, str) and isinstance(thread, str)
        for part in (thread, workspace):
            if not part:
                continue
            if full == part:
                full = ""
            elif full.endswith(f"\n\n{part}"):
                full = full[: -len(part) - 2]
            else:
                return None
        return cls(shared=full, workspace=workspace, thread=thread)

    @classmethod
    def execution_update(
        cls,
        state: Mapping[str, object],
        rendered: SystemPrompt | None,
        *,
        enabled: bool,
        workspace: str,
    ) -> dict[str, str | bool]:
        if not enabled:
            if state.get("execution_prompt_enabled"):
                return {
                    "rendered_system_prompt": str(state["legacy_system_prompt"]),
                    "rendered_workspace_prompt": str(state.get("legacy_workspace_prompt") or ""),
                    "rendered_thread_prompt": str(state.get("legacy_thread_prompt") or ""),
                    "system_prompt_fixed": True,
                    "execution_prompt_enabled": False,
                }
            return rendered.state() if rendered is not None else {}
        baseline = rendered or cls.from_state(state)
        shared = state.get("execution_shared_prompt")
        thread = state.get("execution_thread_prompt")
        if not isinstance(shared, str) or not isinstance(thread, str):
            if baseline is None:
                raise ValueError("An unsegmented legacy prompt must be rendered before activation")
            shared, thread = baseline.shared, baseline.thread
        updated = cls(shared=shared, workspace=workspace, thread=thread).state()
        if state.get("execution_prompt_enabled") and all(
            state.get(k) == v for k, v in updated.items()
        ):
            return {}
        saved: dict[str, str | bool] = {}
        if "legacy_system_prompt" not in state:
            legacy = rendered.state() if rendered is not None else state
            saved = {
                "legacy_system_prompt": str(
                    state.get("rendered_system_prompt")
                    or legacy.get("rendered_system_prompt")
                    or ""
                ),
                "legacy_workspace_prompt": str(legacy.get("rendered_workspace_prompt") or ""),
                "legacy_thread_prompt": str(legacy.get("rendered_thread_prompt") or ""),
            }
        return {
            **updated,
            **saved,
            "execution_shared_prompt": shared,
            "execution_thread_prompt": thread,
            "execution_prompt_enabled": True,
        }

    def state(self) -> dict[str, str | bool]:
        """What the thread's state keeps: the whole prompt, which ends in the workspace's
        part and then the thread's, and those parts, which the runtime caches apart."""
        parts = (self.shared, self.workspace, self.thread)
        return {
            "rendered_system_prompt": "\n\n".join(p for p in parts if p),
            "rendered_workspace_prompt": self.workspace,
            "rendered_thread_prompt": self.thread,
            "system_prompt_fixed": True,
        }


def construct_system_prompt(
    dashboard_base_url: str = "",
    artifact_url: str | None = None,
    default_repo: dict[str, str] | None = None,
    repo_custom_instructions: str | None = None,
    workspace_name: str | None = None,
    workspace_instructions: str | None = None,
    admin_workspaces: bool = False,
    sole_writer: bool = False,
    continued_from_collaborative: bool = False,
    workspace_repos: list[str] | None = None,
) -> SystemPrompt:
    """Render the retained legacy baseline before run-pinned execution guidance is applied."""
    shared = prompt(
        "system/main",
        dashboard_context_section=prompt(
            "system/dashboard-context",
            environment=ENV.OPENSWE_ENV.optional(),
            dashboard_base_url=dashboard_base_url or "(dashboard URL unavailable)",
        ),
        self_awareness_section=prompt(
            "system/self-awareness",
            source_repo=ENV.OPENSWE_SOURCE_REPO.get(),
            source_path=ENV.OPENSWE_SOURCE_PATH.get().strip("/"),
        ),
        default_prompt_section=_load_default_prompt(),
        collaboration_section=_render_collaboration_section(),
        external_untrusted_comments_section=EXTERNAL_UNTRUSTED_COMMENTS_SECTION,
    )
    thread = prompt(
        "system/thread",
        artifact_url=artifact_url or "(artifact link unavailable)",
        default_repo=(
            f"{default_repo['owner']}/{default_repo['name']}"
            if default_repo and default_repo.get("owner") and default_repo.get("name")
            else ""
        ),
        continued_from_collaborative=continued_from_collaborative,
        repo_instructions_section=_render_repo_instructions_section(repo_custom_instructions),
        admin_workspaces=admin_workspaces,
        sole_writer=sole_writer,
    )
    workspace = _render_workspace_section(workspace_name, workspace_instructions, workspace_repos)
    return SystemPrompt(shared=shared, workspace=workspace, thread=thread)


@dataclass(frozen=True)
class TurnContext:
    """Where a turn runs and where it came from: guidance the system prompt leaves out.

    Each part reaches the model as a ``run`` context block, added when it differs
    from the one the model last saw.

    ``local_checkout`` says the working directory already *is* the user's own
    repository — a thread bridged to their machine — so the clone-or-sync and
    git-identity steps a hosted sandbox needs would rewrite their checkout.
    ``local_checkout_client`` is the app serving that machine: only the CLI
    prints a result the run must hand it.
    """

    working_dir: str
    working_folder: bool = False
    source: str = "dashboard"
    slack_context: bool = False
    automation_slack_reply: bool = False
    slack_ask: bool = False
    slack_breakout: bool = False
    slack_by_the_way: bool = False
    slack_follow_up_suggestions: bool = False
    implementation_walkthrough_enabled: bool = False
    sandbox_file_downloads: bool = False
    local_access: bool = False
    local_checkout: bool = False
    local_checkout_client: BridgeClient = "cli"
    # Why the run's managed tool catalog could not be read, if it could not.
    tool_catalog_note: str = ""
    # The sender's other recent threads, whose activity changes from turn to turn.
    recent_threads: str = ""

    def environment(self) -> str:
        environment = prompt(
            "system/working-environment-local"
            if self.local_checkout
            else "system/working-environment",
            working_dir=self.working_dir,
            desktop=self.local_checkout_client == "desktop",
            working_folder=self.working_folder,
        )
        if self.local_access:
            environment += "\n\n" + prompt("system/local-access")
        if self.implementation_walkthrough_enabled:
            environment += "\n\n" + prompt("system/implementation-walkthrough")
        if self.sandbox_file_downloads:
            environment += "\n\n" + prompt("system/sandbox-file-downloads")
        if note := self.tool_catalog_note.strip():
            environment += "\n\n" + note
        return environment

    def source_guidance(self) -> str:
        sections = [prompt("system/source-context", source_guidance=self._source_guidance())]
        if not self.local_checkout and (scope := _render_repository_scope_section()):
            sections.append(scope)
        return "\n\n".join(sections)

    def _source_guidance(self) -> str:
        if self.source == "background_task":
            name = "background-task"
        elif self.source == "slack" and self.slack_context:
            name = (
                "slack-by-the-way"
                if self.slack_by_the_way
                else "slack-ask"
                if self.slack_ask
                else "slack"
            )
        elif self.source in {"linear", "github", "schedule", "dashboard"}:
            name = self.source
        else:
            name = "generic"
        if name in {"slack", "schedule"}:
            guidance = prompt(
                f"system/source-{name}",
                breakout=self.slack_breakout,
                slack=self.slack_context,
                automation_slack_reply=self.automation_slack_reply,
            )
        else:
            guidance = prompt(f"system/source-{name}")
        guidance = f"<open_swe_source_context>\n{guidance}\n</open_swe_source_context>"
        if self.slack_context and self.slack_follow_up_suggestions:
            guidance += "\n" + prompt("system/slack-follow-up-suggestions")
        return guidance

    def messages(self, visible: Collection[str]) -> list[RunMessage]:
        """The blocks the model does not see as they now are, given the ``visible`` context hashes."""
        guidance = {"environment": self.environment(), "source": self.source_guidance()}
        if self.recent_threads:
            guidance["recent-threads"] = self.recent_threads
        blocks = {
            context_id: run_context(context_id, text) for context_id, text in guidance.items()
        }
        return [
            {
                "role": "user",
                "content": block,
                "additional_kwargs": Provenance(kind="context", sender=f"run:{context_id}").stamp(),
            }
            for context_id, block in blocks.items()
            if dynamic_context_hash(block) not in visible
        ]
