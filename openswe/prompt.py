import logging
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from runtime_api.provenance import Provenance

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


def _render_collaboration_section() -> str:
    return prompt(
        "system/collaboration",
        bot_coauthor_trailer=f"Co-authored-by: {OPEN_SWE_BOT_NAME} <{OPEN_SWE_BOT_EMAIL}>",
        pr_attribution_text=PR_ATTRIBUTION_TEXT,
    )


@dataclass(frozen=True)
class SystemPrompt:
    """Cacheable shared, execution and thread prompt components."""

    shared: str
    execution: str = ""
    thread: str = ""

    @staticmethod
    def execution_section(instructions: str, repos: list[str]) -> str:
        if not instructions.strip() and not repos:
            return ""
        return prompt(
            "system/execution-instructions", instructions=instructions.strip(), repos=repos
        )

    @classmethod
    def from_state(cls, state: Mapping[str, object]) -> SystemPrompt | None:
        full = state.get("rendered_system_prompt")
        execution = state.get("rendered_execution_prompt")
        thread = state.get("rendered_thread_prompt")
        if not all(isinstance(part, str) for part in (full, execution, thread)):
            return None
        assert isinstance(full, str) and isinstance(execution, str) and isinstance(thread, str)
        for part in (thread, execution):
            if not part:
                continue
            if full == part:
                full = ""
            elif full.endswith(f"\n\n{part}"):
                full = full[: -len(part) - 2]
            else:
                return None
        return cls(shared=full, execution=execution, thread=thread)

    @classmethod
    def execution_update(
        cls,
        state: Mapping[str, object],
        rendered: SystemPrompt | None,
        *,
        execution: str,
    ) -> dict[str, str | bool]:
        baseline = rendered or cls.from_state(state)
        if baseline is None:
            raise ValueError("The system prompt must be rendered before applying instructions")
        updated = cls(shared=baseline.shared, execution=execution, thread=baseline.thread).state()
        return {} if all(state.get(key) == value for key, value in updated.items()) else updated

    def state(self) -> dict[str, str | bool]:
        """The whole prompt and its independently cached components."""
        parts = (self.shared, self.execution, self.thread)
        return {
            "rendered_system_prompt": "\n\n".join(p for p in parts if p),
            "rendered_execution_prompt": self.execution,
            "rendered_thread_prompt": self.thread,
            "system_prompt_fixed": True,
        }


def construct_system_prompt(
    dashboard_base_url: str = "",
    artifact_url: str | None = None,
    default_repo: dict[str, str] | None = None,
    repo_custom_instructions: str | None = None,
    admin: bool = False,
    sole_writer: bool = False,
    continued_from_collaborative: bool = False,
) -> SystemPrompt:
    """Render shared instructions and the current thread context."""
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
        admin=admin,
        sole_writer=sole_writer,
    )
    return SystemPrompt(shared=shared, thread=thread)


@dataclass(frozen=True)
class TurnContext:
    """Where a turn runs and where it came from: guidance the system prompt leaves out.

    Each part reaches the model as a ``run`` context block, added when it differs
    from the one the model last saw.
    """

    working_dir: str
    source: str = "dashboard"
    slack_context: bool = False
    automation_slack_reply: bool = False
    slack_ask: bool = False
    slack_breakout: bool = False
    slack_by_the_way: bool = False
    slack_follow_up_suggestions: bool = False
    implementation_walkthrough_enabled: bool = False
    local_access: bool = False
    # The thread ran on its owner's machine before it moved to a cloud sandbox.
    moved_off_mac: bool = False
    # Why the run's managed tool catalog could not be read, if it could not.
    tool_catalog_note: str = ""
    # The sender's other recent threads, whose activity changes from turn to turn.
    recent_threads: str = ""

    def environment(self) -> str:
        environment = prompt(
            "system/working-environment",
            working_dir=self.working_dir,
        )
        if self.local_access:
            environment += "\n\n" + prompt("system/local-access")
        if self.moved_off_mac:
            environment += "\n\n" + prompt("system/moved-off-mac")
        if self.implementation_walkthrough_enabled:
            environment += "\n\n" + prompt("system/implementation-walkthrough")
        environment += "\n\n" + prompt("system/sandbox-file-downloads")
        if note := self.tool_catalog_note.strip():
            environment += "\n\n" + note
        return environment

    def source_guidance(self) -> str:
        sections = [prompt("system/source-context", source_guidance=self._source_guidance())]
        if scope := _render_repository_scope_section():
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
