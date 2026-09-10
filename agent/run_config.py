"""Open SWE's ``configurable`` fields, on top of the generic run contract.

The parsing rules — unknown keys survive, parsing never raises, everything is
optional — live in :mod:`coding_agent.run_config`, alongside the fields any
coding agent has. This module adds the platform ones: the repository, the
GitHub/Slack/Linear origin of the run, the pull request under review, and the
reviewer, chat, and analyzer knobs.
"""

import logging
from collections.abc import Mapping
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, ValidationError

from agent.source_context import GitHubIssueRef, LinearIssueRef, SlackThreadRef
from coding_agent.run_config import Int, RunConfig

logger = logging.getLogger(__name__)


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


class OpenSWERunConfig(RunConfig):
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
    reviewer_model_id: str | None = None
    reviewer_reasoning_effort: str | None = None
    reviewer_subagent_model_id: str | None = None
    reviewer_subagent_reasoning_effort: str | None = None
    grouping_model_id: str | None = None
    grouping_reasoning_effort: str | None = None

    # Behavior toggles
    draft_prs: bool | None = None
    admin_thread: bool | None = None

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
    reviewer_eval: bool | None = None
    reviewer_eval_cap: Int | None = None
    reviewer_eval_severity_threshold: str | None = None

    # Background jobs
    automation_slack_notification: AutomationSlackNotification | None = None

    @property
    def repo_full_name(self) -> str:
        return self.repo.full_name if self.repo else ""

    @property
    def is_eval(self) -> bool:
        return self.eval is True or self.reviewer_eval is True
