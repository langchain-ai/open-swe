"""The shape of ``configurable`` — the per-run contract every graph reads.

``configurable`` rides in the ``RunnableConfig`` of every agent, reviewer, and
analyzer run. It is assembled by webhooks, the dashboard, and cron launchers,
merged and re-written at several hops, and then read in ~40 modules.

The same three rules that govern :mod:`agent.source_context` apply here, for the
same reasons:

**Unknown keys survive.** Writers add keys this module has never heard of, and
call sites read a configurable, add to it, and pass it on. ``extra="allow"``
plus :meth:`RunConfig.dump` (which excludes unset fields) keeps that round-trip
byte-identical.

**Parsing never raises, and never loses more than it has to.** Nothing validates
``configurable`` on write, so a single malformed value must not cost the run its
``thread_id``. A field that fails validation is dropped and the rest is kept.

**Everything is optional.** Which keys are present depends on the graph and the
trigger; a reviewer run has no ``agent_model_id`` and a Slack run has no
``chat_pr_number``.
"""

import logging
from collections.abc import Mapping
from typing import Annotated, Any, Self

from langgraph.config import get_config
from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError

from agent.source_context import GitHubIssueRef, LinearIssueRef, SlackThreadRef

logger = logging.getLogger(__name__)


def _reject_bool(value: Any) -> Any:
    """Bools are ints to pydantic, so ``pr_number=True`` would silently mean PR 1."""
    if isinstance(value, bool):
        raise ValueError("bool is not a valid integer here")
    return value


Int = Annotated[int, BeforeValidator(_reject_bool)]


class Repo(BaseModel):
    """A GitHub repository as ``configurable["repo"]`` carries it."""

    model_config = ConfigDict(extra="allow")

    owner: str = ""
    name: str = ""

    @classmethod
    def parse(cls, raw: Any) -> Self | None:
        if isinstance(raw, cls):
            return raw
        if not isinstance(raw, Mapping):
            return None
        try:
            return cls.model_validate(dict(raw))
        except ValidationError:
            logger.warning("Unparseable repo config, ignoring", exc_info=True)
            return None

    @property
    def full_name(self) -> str:
        """``owner/name``, or ``""`` when either half is missing."""
        return f"{self.owner}/{self.name}" if self.owner and self.name else ""

    def __bool__(self) -> bool:
        return bool(self.owner and self.name)


class GitHubPROrIssueRef(BaseModel):
    """A cross-repo PR/issue target, which may name a repo other than the run's."""

    model_config = ConfigDict(extra="allow")

    number: Int | None = None
    repo: Repo | None = None


class AutomationSlackNotification(BaseModel):
    model_config = ConfigDict(extra="allow")

    channel_id: str = ""
    mode: str = ""
    schedule_id: str = ""
    schedule_name: str | None = None


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Identity and provenance
    thread_id: str | None = None
    run_id: str | None = None
    prepare_run_id: str | None = None
    source: str | None = None
    task: str | None = None
    environment: str | None = None
    local_project_path: str | None = None

    # Actor
    github_login: str | None = None
    github_user_id: str | None = None
    user_email: str | None = None

    # Repository
    repo: Repo | None = None
    repo_private: bool | None = None
    repo_explicitly_none: bool | None = None
    branch_name: str | None = None

    # Where the run came from
    slack_thread: SlackThreadRef | None = None
    linear_issue: LinearIssueRef | None = None
    github_issue: GitHubIssueRef | None = None
    github_pr_or_issue: GitHubPROrIssueRef | None = None

    # Pull request under review
    pr_number: Int | None = None
    pr_url: str | None = None
    head_sha: str | None = None
    base_sha: str | None = None
    last_reviewed_sha: str | None = None
    re_review: bool | None = None
    diff_text: str | None = None
    diff_line_set: dict[str, Any] | None = None

    # Reviewer run shape
    reviewer_event: str | None = None
    reviewer_thread_id: str | None = None
    finding_reply_id: str | None = None
    finding_reply_body: str | None = None
    finding_reply_author: str | None = None
    review_trace_link_enabled: bool | None = None

    # Model selection
    agent_model_id: str | None = None
    agent_effort: str | None = None
    reviewer_model_id: str | None = None
    reviewer_reasoning_effort: str | None = None
    reviewer_subagent_model_id: str | None = None
    reviewer_subagent_reasoning_effort: str | None = None
    grouping_model_id: str | None = None
    grouping_reasoning_effort: str | None = None

    # Behavior toggles
    plan_mode: bool | None = None
    draft_prs: bool | None = None
    admin_thread: bool | None = None
    stop_summary: bool | None = None

    # Dashboard review chat
    chat_repo_owner: str | None = None
    chat_repo_name: str | None = None
    chat_pr_number: Int | None = None
    chat_head_sha: str | None = None
    chat_model_id: str | None = None
    chat_effort: str | None = None
    chat_github_token: str | None = None

    # Review-style analyzer
    analyzer_mode: str | None = None
    review_style_full_name: str | None = None
    review_style_github_token: str | None = None
    review_style_top_reviewers: list[str] | None = None
    review_style_samples_text: str | None = None
    review_style_reviews_sampled: Int | None = None
    review_style_prs_sampled: Int | None = None

    # Eval harness
    eval: bool | None = None
    reviewer_eval: bool | None = None
    reviewer_eval_cap: Int | None = None
    reviewer_eval_severity_threshold: str | None = None

    # Background jobs
    watch_key: str | None = None
    schedule_id: str | None = None
    automation_slack_notification: AutomationSlackNotification | None = None

    @classmethod
    def parse(cls, raw: Any) -> Self:
        """Parse a ``configurable`` mapping, dropping only the fields that fail."""
        if isinstance(raw, cls):
            return raw
        if not isinstance(raw, Mapping):
            return cls()
        data = dict(raw)
        for _ in range(len(cls.model_fields) + 1):
            try:
                return cls.model_validate(data)
            except ValidationError as exc:
                dropped = {
                    str(error["loc"][0])
                    for error in exc.errors()
                    if error.get("loc") and str(error["loc"][0]) in data
                }
                if not dropped:
                    logger.warning("Unparseable configurable, ignoring", exc_info=True)
                    return cls()
                logger.warning("Dropping unparseable configurable keys: %s", sorted(dropped))
                for key in dropped:
                    del data[key]
        return cls()

    @classmethod
    def from_config(cls, config: Any) -> Self:
        """Parse the ``configurable`` out of a ``RunnableConfig``."""
        if not isinstance(config, Mapping):
            return cls()
        return cls.parse(config.get("configurable"))

    @classmethod
    def from_runtime(cls) -> Self:
        """Parse the running graph's own ``configurable``."""
        return cls.from_config(get_config())

    def dump(self, *, include: set[str] | None = None) -> dict[str, Any]:
        """The JSON value to store, preserving exactly the keys that were set."""
        return self.model_dump(mode="json", exclude_unset=True, include=include)

    def get(self, key: str) -> Any:
        """Value for ``key``, whether it is a declared field or an extra."""
        if key in type(self).model_fields:
            return getattr(self, key)
        return (self.model_extra or {}).get(key)

    @property
    def repo_full_name(self) -> str:
        return self.repo.full_name if self.repo else ""

    @property
    def is_eval(self) -> bool:
        return self.eval is True or self.reviewer_eval is True
