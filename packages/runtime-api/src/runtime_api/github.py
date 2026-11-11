"""The calls MDA's GitHub channel makes on the backend, with no run yet.

The channel receives Open SWE's GitHub App deliveries in the MDA deployment and
hands every one to the backend, which runs its own work for it. The channel decides
only what starts an agent run, and asks the backend for what needs its data, its
GitHub App or its signing key:

- ``github_delivery_facts``: for a delivery that may start an agent run, whether
  a workspace owns its repository, whether the allowlist takes it, whether the
  public-repo gate turned the sender away, and the handles that mention Open SWE.
  The backend gets every delivery itself, forwarded as GitHub sent it
  (``forward_to``, served at ``/mda/github/deliveries``), and logs it and runs its
  own work there; ``record_github_delivery`` does the same for a channel deployed
  before it forwarded, and answers the facts too.
- ``github_automation_rules``: the automations with a GitHub trigger on the
  delivery's repository, and whether the issue's or pull request's author is an
  Open SWE user; the channel matches them and writes each match's prompt.
- ``github_issue_facts``: for an ``@openswe`` mention on an issue, which of the
  people its prompt quotes are Open SWE users, whether the sender's email is known,
  whether a run already took the issue in, and the thread's workspace; the channel
  writes the run's input.
- ``act_on_github_delivery``: claims and signs the runs of the automations the
  channel matched, each on a thread of its own;
  routes a pull request comment, planning its run when it goes to the pull
  request's own thread; and for an issue mention, checks the sender again, indexes
  the issue's thread and signs the run the channel wrote. The channel starts the
  planned runs.

So a delivery that starts no agent run takes no call, and one that may takes one,
and a call or three more when it mentions Open SWE or fires automations.
"""

import json
from typing import Annotated, Final, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, field_validator, model_validator

from runtime_api.endpoint import CHANNEL_API_PREFIX, Endpoint

# GitHub's deliveries are at most 25 MB; anything near this is no event Open SWE acts on.
MAX_PAYLOAD_CHARS: Final = 5_000_000
# Well above a written prompt: an automation's own is at most 20,000 characters, and
# the issue or pull request it quotes is a GitHub body, at most 65,536.
MAX_AUTOMATION_PROMPT_CHARS: Final = 100_000
# ``owner/name``, as GitHub spells a repository.
REPOSITORY_PATTERN: Final = r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$"
# A GitHub login, such as ``octocat`` or ``openswe[bot]``.
LOGIN_PATTERN: Final = r"^[A-Za-z0-9-]{1,39}(\[bot\])?$"
# More people than one issue's comments name; a longer list is no issue's.
MAX_LOGINS: Final = 500


class GitHubDelivery(BaseModel):
    """A delivery the channel verified, as GitHub sent it."""

    # GitHub's event name (``X-GitHub-Event``), such as ``pull_request``.
    event: str = Field(min_length=1, max_length=64)
    delivery_id: str = Field(min_length=1, max_length=128)
    payload: dict[str, JsonValue]

    @field_validator("payload")
    @classmethod
    def _bounded(cls, payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if len(json.dumps(payload)) > MAX_PAYLOAD_CHARS:
            raise ValueError("payload is too large for a GitHub delivery")
        return payload


class GitHubDeliveryFacts(BaseModel):
    """What Open SWE knows about a delivery's repository, for the channel to route it."""

    # A workspace owns the repository, or the delivery names none.
    routable: bool
    # The repository allowlist takes it; a delivery it doesn't take starts nothing.
    allowed: bool
    # The public-repo gate turned away the sender of a pull request opened, reopened,
    # marked ready or pushed to: the delivery starts nothing but a merge queue wakeup.
    gated: bool = False
    # Automatic review is on for the repository; only read for pull requests and pushes,
    # and only by the backend's own work.
    auto_review: bool
    # The handles that mention Open SWE, lowercased, such as ``@openswe``.
    tags: list[str]


# The backend's work a delivery can start, each as its webhook ran it
# (``openswe.github.work``).
type GitHubWorkName = Literal[
    # The native merge queue reads the queue again.
    "wake_merge_queue",
    # The App was installed, removed or suspended, or its repositories changed:
    # everyone's cached list of the repositories they reach through it is dropped.
    "forget_cached_repos",
    # The agent thread that opened a pull request learns its state, and its usage.
    "track_pull_request_state",
    "track_pull_request_usage",
    # A push to a pull request's branch: superseded approvals withdrawn, its guides
    # paused, and a fork's head reviewed again.
    "withdraw_superseded_approvals",
    "notify_pr_updated",
    "review_fork_push",
    "settle_review_on_close",
    # A pull request closed: what reviews saved to apps for it is deleted.
    "delete_app_documents_on_close",
    "settle_human_reviews",
    "update_reviewer_watch",
    # The walkthrough plans an open pull request, drafts included, past the public-repo gate.
    "walkthrough_pull_request",
    # A review thread resolved or unresolved: the reviewer's findings follow it.
    "sync_review_thread",
    # Automatic review of a pull request opened or marked ready, behind the public-repo gate.
    "review_pull_request",
    "review_push",
    "ci_event",
    "record_completed_review",
    # The ``Robin Approves`` check is posted again on a pull request's head.
    "evaluate_approval_policy",
]


# The GitHub events an automation's trigger fires on.
GitHubTriggerEvent = Literal[
    "issues.opened", "pull_request.opened", "pull_request.closed", "pull_request.merged"
]

type _Login = Annotated[str, Field(pattern=LOGIN_PATTERN)]


class GitHubAutomationRulesRequest(BaseModel):
    """The automations an issue or pull request event on ``repository`` may fire."""

    repository: str = Field(pattern=REPOSITORY_PATTERN)
    # Who opened the issue or pull request, whose words the prompt quotes.
    author: str = Field(default="", max_length=64)


class GitHubAutomation(BaseModel):
    id: str = Field(min_length=1)
    # What the automation asks its run to do, which the channel's prompt leads with.
    prompt: str
    # The events its triggers on the repository fire on.
    events: list[GitHubTriggerEvent]


class GitHubAutomationRules(BaseModel):
    """The enabled automations with a GitHub trigger on the repository."""

    automations: list[GitHubAutomation]
    # Whether the author is an Open SWE user: otherwise the channel fences the issue
    # or pull request it quotes as untrusted.
    author_registered: bool = False


class FiredGitHubAutomation(BaseModel):
    """An automation the channel matched a delivery to, the event it fires on, and the
    prompt the channel wrote for its run."""

    automation_id: str = Field(min_length=1)
    event: GitHubTriggerEvent
    prompt: str = Field(min_length=1, max_length=MAX_AUTOMATION_PROMPT_CHARS)


class GitHubIssueFactsRequest(BaseModel):
    """What Open SWE knows about an ``@openswe`` mention on an issue."""

    # The issue's conversation thread.
    thread_id: UUID
    # Who mentioned Open SWE, as the delivery's sender.
    sender: _Login
    # The people whose words the run's prompt quotes, the sender among them.
    logins: list[_Login] = Field(max_length=MAX_LOGINS)


class GitHubIssueFacts(BaseModel):
    # Whether a run on the issue's thread already took the issue in: the mention is a
    # follow-up, not the issue itself.
    conversation_started: bool
    # Whether Open SWE knows the sender's email, which the run acts on GitHub as.
    sender_email_known: bool
    # Those of ``logins`` who are Open SWE users, lowercased: anyone else's words are
    # fenced as untrusted.
    trusted: list[str]
    # The workspace the thread started in, which a follow-up stays in.
    thread_workspace: str | None = None


class GitHubIssueRunRequest(BaseModel):
    """An ``@openswe`` mention on an issue: sign the run it starts on the issue's thread."""

    # The issue's conversation thread, which the channel starts the run on.
    thread_id: UUID
    # The thread waits on an interrupt: the backend only says whether Open SWE would act.
    waiting: bool = False
    # The run's input, which the channel wrote; none while the thread waits.
    input: dict[str, JsonValue] | None = None
    # The workspace the run works in; ``None`` for the one that owns the repository.
    workspace: str | None = None

    @model_validator(mode="after")
    def _written(self) -> Self:
        if not self.waiting and self.input is None:
            raise ValueError("a run that starts needs its input")
        return self


class ActOnGitHubDelivery(GitHubDelivery):
    """What the channel decided a delivery starts."""

    # The backend's work, as a channel deployed before the backend picked its own still
    # names it; the backend runs a delivery's work once either way.
    work: list[GitHubWorkName] = []
    # A pull request comment, review or review comment to route: who wrote it,
    # whether it answers a review finding or wakes the agent thread that opened the
    # pull request, the public-repo gate, then its run.
    comment: bool = False
    # Whether that comment mentions Open SWE.
    mentioned: bool = False
    # The automations the channel matched the delivery to, with their prompts.
    automations: list[FiredGitHubAutomation] = Field(default=[], max_length=100)
    issue_run: GitHubIssueRunRequest | None = None

    @model_validator(mode="after")
    def _acts(self) -> Self:
        if not (self.work or self.comment or self.automations or self.issue_run):
            raise ValueError("a delivery that starts nothing needs no call")
        return self


class GitHubIssueRun(BaseModel):
    """The signed run, which starts with the input the channel wrote."""

    context: dict[str, JsonValue]
    metadata: dict[str, JsonValue]
    webhook: str | None = None


class PlannedGitHubIssueRun(BaseModel):
    # Whether Open SWE acts: false when the commenter isn't an Open SWE user, the
    # public-repo gate refuses the sender, or the issue gives the agent nothing to do.
    starts: bool
    # The run, unless the thread waits.
    run: GitHubIssueRun | None = None


class GitHubThreadRun(BaseModel):
    """Start the run on ``thread_id``: an automation's thread of its own for this
    delivery, or the pull request's own thread for a comment's."""

    thread_id: UUID
    input: dict[str, JsonValue]
    context: dict[str, JsonValue]
    metadata: dict[str, JsonValue]
    webhook: str | None = None


class GitHubDeliveryActed(BaseModel):
    """The backend took the work, which it runs after answering, as it did for its webhook."""

    # The issue's run, when the call asked for one.
    issue_run: PlannedGitHubIssueRun | None = None
    # A run per automation the channel matched that starts, each claimed once per
    # delivery. A call sent again plans the runs it already claimed on the same
    # threads, where the channel starts each delivery's run once.
    automations: list[GitHubThreadRun] = []
    # A comment's run on the pull request's own thread, the channel's. A comment the
    # backend starts a run for itself, on the agent's thread or one the backend
    # owns, has none; its run starts once per delivery.
    comment_run: GitHubThreadRun | None = None


RECORD_GITHUB_DELIVERY: Final = Endpoint(
    "record_github_delivery",
    GitHubDelivery,
    GitHubDeliveryFacts,
    seconds=60.0,
    # Recorded once per delivery ID, so a repeat logs nothing again.
    delivery="idempotent",
    prefix=CHANNEL_API_PREFIX,
)
GITHUB_DELIVERY_FACTS: Final = Endpoint(
    "github_delivery_facts",
    GitHubDelivery,
    GitHubDeliveryFacts,
    seconds=60.0,
    # Reads only: a repeat answers the same.
    delivery="idempotent",
    prefix=CHANNEL_API_PREFIX,
)
GITHUB_AUTOMATION_RULES: Final = Endpoint(
    "github_automation_rules",
    GitHubAutomationRulesRequest,
    GitHubAutomationRules,
    seconds=60.0,
    # Only reads.
    delivery="idempotent",
    prefix=CHANNEL_API_PREFIX,
)
GITHUB_ISSUE_FACTS: Final = Endpoint(
    "github_issue_facts",
    GitHubIssueFactsRequest,
    GitHubIssueFacts,
    seconds=60.0,
    # Only reads.
    delivery="idempotent",
    prefix=CHANNEL_API_PREFIX,
)
ACT_ON_GITHUB_DELIVERY: Final = Endpoint(
    "act_on_github_delivery",
    ActOnGitHubDelivery,
    GitHubDeliveryActed,
    # Each automation's planning signs a run; a repository rarely fires more than a few at once.
    seconds=120.0,
    # The work is claimed once per delivery ID, but planning an issue's run indexes
    # the thread and marks it busy, so a call isn't sent again blindly.
    prefix=CHANNEL_API_PREFIX,
)
