"""Live GitHub pull-request health for dashboard threads."""

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Collection, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any, ClassVar, Literal, Self
from urllib.parse import quote

import httpx2
from fastapi import HTTPException
from pydantic import (
    AliasPath,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)
from pydantic.alias_generators import to_camel

from openswe.github.branch_config import BranchConfig
from openswe.github.changed_files import CHANGED_FILES, FileList, PullRequestTips
from openswe.github.checks import APPROVAL_CHECK_RUN_NAME
from openswe.github.ci import unreported_required_checks
from openswe.github.http import (
    COMMIT_CI_FRAGMENT,
    GITHUB_API_BASE,
    GITHUB_GRAPHQL,
    CommitCI,
    GitHubClient,
    GitHubError,
    GraphqlCheckApp,
    GraphQLError,
    GraphqlNode,
    GraphqlPageInfo,
    RepoClient,
    or_none,
)
from openswe.github.metering import MeteredTransport
from openswe.github.org_membership import OPEN_SWE_AUTHOR_LOGINS, OPEN_SWE_GITHUB_LOGINS
from openswe.github.queue import QueuePull, Review, ReviewPage

logger = logging.getLogger(__name__)

_FILE_LIST = TypeAdapter(FileList)
_OWNER_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_REPO_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,100}")
_SEARCH_PAGE_SIZE = 100
# GitHub search returns at most 1000 results per query.
_SEARCH_MAX_PAGES = 10
_MERGEABILITY_ATTEMPTS = 3
_MERGEABILITY_DELAY_SECONDS = 0.7
_SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40,64}")
_FAILING_CHECK_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "action_required", "startup_failure"}
)
_INCONCLUSIVE_CHECK_CONCLUSIONS = frozenset({"cancelled", "stale", "skipped", "neutral"})
_MERGEABILITY_QUERY = """
query PullRequestMergeability($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      mergeable
      mergeStateStatus
    }
  }
}
"""
# ``commits(last: 1)`` is the head unless the pull holds more commits than GitHub lists.
_PULL_CI_QUERY = (
    """
query PullRequestCI($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      state
      headRefOid
      headRefName
      baseRefName
      commits(last: 1) { nodes { commit { ...CommitCI } } }
    }
  }
}
"""
    + COMMIT_CI_FRAGMENT
)
_TIPS_QUERY = """
query PullRequestTips($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) { baseRefOid headRefOid }
  }
}
"""
_TITLE_AND_BODY_QUERY = """
query PullRequestTitleAndBody($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) { title body }
  }
}
"""
_REVIEW_THREADS_QUERY = """
query PullRequestReviewThreads($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          path
          line
          originalLine
          comments(first: 50) {
            nodes {
              author { login }
              body: bodyText
              url
            }
          }
        }
      }
    }
  }
}
"""
_REVIEW_THREAD_ROOTS_QUERY = """
query PullRequestReviewThreadRoots($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved isOutdated
          comments(first: 1) { nodes { fullDatabaseId } }
        }
      }
    }
  }
}
"""
_ALL_REVIEW_THREADS_QUERY = """
query PullRequestAllReviewThreads($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved isOutdated path line startLine diffSide startDiffSide
          comments(first: 100) {
            nodes { id fullDatabaseId body pullRequestReview { fullDatabaseId } }
          }
        }
      }
    }
  }
}
"""
_REVIEW_THREAD_STATES_QUERY = """
query PullRequestFindingThreads($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved }
      }
    }
  }
}
"""
_ADD_REVIEW_THREAD_MUTATION = """
mutation($input: AddPullRequestReviewThreadInput!) {
  addPullRequestReviewThread(input: $input) { thread { id } }
}
"""
_RESOLVE_THREAD_MUTATION = """
mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { isResolved } } }
"""
_UNRESOLVE_THREAD_MUTATION = """
mutation($id: ID!) { unresolveReviewThread(input: {threadId: $id}) { thread { isResolved } } }
"""
_UPDATE_REVIEW_COMMENT_MUTATION = """
mutation($input: UpdatePullRequestReviewCommentInput!) {
  updatePullRequestReviewComment(input: $input) { pullRequestReviewComment { id } }
}
"""
# Each suite's runs come in one page: a suite with more falls back to REST
# (``HeadChecks.runs``), since dropping a run could hide a failed redeploy.
_HEAD_CHECKS_QUERY = """
query PullRequestHeadChecks($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      state
      headRefOid
      commits(last: 1) {
        nodes {
          commit {
            oid
            checkSuites(first: 100, after: $cursor) {
              pageInfo { hasNextPage endCursor }
              nodes {
                databaseId
                app { databaseId slug }
                workflowRun { workflow { databaseId } }
                checkRuns(first: 100, filterBy: { checkType: ALL }) {
                  pageInfo { hasNextPage }
                  nodes { databaseId name conclusion }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""
_STATUS_REVIEW_FRAGMENT = """
fragment StatusReview on PullRequestReview {
  fullDatabaseId state
  author {
    __typename login avatarUrl
    ... on User { databaseId }
    ... on Bot { databaseId }
  }
}
"""
# Everything the dashboard shows about a pull request, in one read. The head commit's
# check runs and statuses, reviews and review threads are first pages; ``_StatusRead``
# says when one ran over, and the caller reads the rest on its own.
_STATUS_QUERY = (
    """
query PullRequestStatus(
  $owner: String!, $repo: String!, $number: Int!, $threadComments: Boolean!, $ci: Boolean!
) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      title state isDraft createdAt updatedAt additions deletions
      author { __typename login }
      mergeable mergeStateStatus
      autoMergeRequest { enabledAt }
      isMergeQueueEnabled isInMergeQueue
      headRefName headRefOid baseRefName reviewDecision
      reviews(first: 100) {
        pageInfo { hasNextPage endCursor }
        nodes { ...StatusReview }
      }
      reviewThreads(first: 100) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved path line originalLine
          comments(first: 50) @include(if: $threadComments) {
            nodes { author { login } body: bodyText url }
          }
        }
      }
      commits(last: 1) { nodes { commit { oid ...CommitCI @include(if: $ci) } } }
    }
  }
}
"""
    + _STATUS_REVIEW_FRAGMENT
    + COMMIT_CI_FRAGMENT
)
_REVIEWS_QUERY = (
    """
query PullRequestReviews($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviews(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { ...StatusReview }
      }
    }
  }
}
"""
    + _STATUS_REVIEW_FRAGMENT
)
_STANDING_APPROVALS_QUERY = """
query PullRequestStandingApprovals($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      headRefOid
      reviews(states: APPROVED, first: 100) {
        pageInfo { hasNextPage }
        nodes { author { login } commit { oid } }
      }
    }
  }
}
"""


CheckState = Literal["passing", "failing", "pending", "unknown", "none"]
ReviewDecision = Literal["approved", "changes_requested", "none"]
ReviewerState = Literal["approved", "changes_requested", "dismissed", "commented"]
PullRequestState = Literal["open", "closed", "merged"]
MergeOutcome = Literal["merged", "auto_merge", "queued"]

_ANY_STATE: frozenset[PullRequestState] = frozenset({"open", "closed", "merged"})


class _GithubReviewUser(BaseModel):
    id: int
    login: str
    avatar_url: str | None = None


class _GithubReview(BaseModel):
    id: int
    state: ReviewerState | Literal["pending"]
    user: _GithubReviewUser | None = None
    commit_id: str | None = None

    @field_validator("state", mode="before")
    @classmethod
    def _lowercase(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value


class PullRequestReviewer(BaseModel):
    """Someone who reviewed the pull request, and where their review stands.

    A comment never overrides an earlier approval or change request, as on GitHub.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    user_id: int
    login: str
    avatar_url: str | None = None
    state: ReviewerState

    @classmethod
    def latest(cls, reviews: Sequence[_GithubReview]) -> list[PullRequestReviewer]:
        latest: dict[int, PullRequestReviewer] = {}
        for review in sorted(reviews, key=lambda review: review.id):
            if review.user is None or review.state == "pending":
                continue
            previous = latest.get(review.user.id)
            if review.state == "commented" and previous and previous.state != "commented":
                continue
            latest[review.user.id] = cls(
                user_id=review.user.id,
                login=review.user.login,
                avatar_url=review.user.avatar_url,
                state=review.state,
            )
        return list(latest.values())

    @staticmethod
    def decision(reviewers: Sequence[PullRequestReviewer]) -> ReviewDecision:
        states = {reviewer.state for reviewer in reviewers}
        if "changes_requested" in states:
            return "changes_requested"
        return "approved" if "approved" in states else "none"


_GITHUB_REVIEWS = TypeAdapter(list[_GithubReview])


ReviewAssignment = Literal[
    "assigned", "requested", "tentative", "mentioned", "draft", "speculative"
]


class OpenPullRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    repo: str
    number: int
    title: str
    state: PullRequestState = "open"
    created_at: str | None = None
    updated_at: str | None = None
    draft: bool | None = None
    author: str | None = None
    details_loading: bool = False
    additions: int | None = None
    deletions: int | None = None
    mergeable: bool | None = None
    merge_state: str = "unknown"
    auto_merge: bool = False
    merge_queue_enabled: bool = False
    in_merge_queue: bool = False
    head_sha: str | None = None
    head_ref: str | None = None
    status_available: bool = False
    ci: CheckState = "unknown"
    required_checks_passing: bool | None = None
    review_decision: ReviewDecision | None = None
    reviewers: list[PullRequestReviewer] | None = None
    review_required: bool = False
    unresolved_threads: int | None = None
    conversation_resolution_required: bool | None = None
    failing_checks: list[str] = Field(default_factory=list)
    pending_checks: list[str] = Field(default_factory=list)
    missing_checks: list[str] = Field(default_factory=list)
    # Why it is on the To Review list; ``None`` on every other list.
    assignment: ReviewAssignment | None = None

    @classmethod
    def listed(
        cls, repo: str, number: int, item: Mapping[str, object], *, details_loading: bool
    ) -> Self:
        """The open pull request as a search hit describes it, without a live read."""
        user = item.get("user")
        return cls(
            repo=repo,
            number=number,
            title=_as_str(item.get("title"), ""),
            created_at=_as_optional_str(item.get("created_at")),
            updated_at=_as_optional_str(item.get("updated_at")),
            draft=_as_optional_bool(item.get("draft")),
            author=_as_optional_str(user.get("login")) if isinstance(user, Mapping) else None,
            details_loading=details_loading,
            additions=_as_optional_int(item.get("additions")),
            deletions=_as_optional_int(item.get("deletions")),
            mergeable=_as_optional_bool(item.get("mergeable")),
            merge_state=_as_str(item.get("mergeable_state"), "unknown"),
            auto_merge=bool(item.get("auto_merge")),
        )


class OpenPullRequests(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    pull_requests: list[OpenPullRequest]
    next_page: int | None
    incomplete: bool
    updated_at: str


@dataclass(frozen=True, slots=True)
class QueuedPullRequestRead:
    """What a To Review list needs of a pull request, with its reviewer's standing review.

    ``standing_review`` is the reviewer's latest ``APPROVED``, ``CHANGES_REQUESTED`` or
    ``DISMISSED`` review, ``None`` when they left none.
    """

    open: bool
    draft: bool | None
    title: str
    created_at: str | None
    updated_at: str | None
    author: str | None
    standing_review: str | None

    BATCH: ClassVar[int] = 50
    _FIELDS: ClassVar[str] = (
        "state isDraft title createdAt updatedAt author { login } "
        "reviews(author: $reviewer, states: [APPROVED, CHANGES_REQUESTED, DISMISSED], last: 1)"
        " { nodes { state } }"
    )

    @classmethod
    async def read(
        cls, github: GitHubClient, refs: Sequence[tuple[str, str, int]], reviewer: str
    ) -> list[Self | None]:
        """Each ``(owner, repo, number)`` in order, ``None`` where GitHub could not read it.

        One GraphQL query per ``BATCH`` pull requests, instead of a REST read of each.
        """
        batches = [refs[start : start + cls.BATCH] for start in range(0, len(refs), cls.BATCH)]
        with MeteredTransport.attributed():
            read = await asyncio.gather(*(cls._batch(github, batch, reviewer) for batch in batches))
        return [found for batch in read for found in batch]

    @classmethod
    async def _batch(
        cls, github: GitHubClient, refs: Sequence[tuple[str, str, int]], reviewer: str
    ) -> list[Self | None]:
        variables: dict[str, object] = {"reviewer": reviewer}
        params = ["$reviewer: String!"]
        fields: list[str] = []
        for i, (owner, repo, number) in enumerate(refs):
            variables |= {f"owner{i}": owner, f"repo{i}": repo, f"number{i}": number}
            params.append(f"$owner{i}: String!, $repo{i}: String!, $number{i}: Int!")
            fields.append(
                f"pull{i}: repository(owner: $owner{i}, name: $repo{i})"
                f" {{ pullRequest(number: $number{i}) {{ {cls._FIELDS} }} }}"
            )
        query = f"query QueuedPullRequests({', '.join(params)}) {{ {' '.join(fields)} }}"
        try:
            # Partial, so a deleted or hidden pull request comes back null beside the others.
            data = await github.graphql(query, variables, partial=True)
        except httpx2.HTTPError, ValueError:
            logger.warning(
                "Could not read queued pull requests", extra={"count": len(refs)}, exc_info=True
            )
            return [None] * len(refs)
        return [cls._of(data.get(f"pull{i}")) for i in range(len(refs))]

    @classmethod
    def _of(cls, repository: object) -> Self | None:
        pull = _graphql_pull({"repository": repository})
        reviews = pull.get("reviews") if pull is not None else None
        nodes = reviews.get("nodes") if isinstance(reviews, Mapping) else None
        if pull is None or not isinstance(nodes, list):
            return None
        author = pull.get("author")
        latest = nodes[-1] if nodes else None
        return cls(
            open=pull.get("state") == "OPEN",
            draft=_as_optional_bool(pull.get("isDraft")),
            title=_as_str(pull.get("title"), ""),
            created_at=_as_optional_str(pull.get("createdAt")),
            updated_at=_as_optional_str(pull.get("updatedAt")),
            author=_as_optional_str(author.get("login")) if isinstance(author, Mapping) else None,
            standing_review=(
                _as_optional_str(latest.get("state")) if isinstance(latest, Mapping) else None
            ),
        )


def _as_str(value: object, default: str) -> str:
    return value if isinstance(value, str) else default


def _as_optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _as_optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def pull_request_identity(record: object) -> tuple[str, str, int] | None:
    if not isinstance(record, Mapping):
        return None
    full_name = record.get("repo_full_name")
    number = record.get("number")
    if not isinstance(full_name, str) or full_name.count("/") != 1:
        return None
    owner, repo = full_name.split("/", 1)
    if (
        not _OWNER_PATTERN.fullmatch(owner)
        or not _REPO_PATTERN.fullmatch(repo)
        or repo in {".", ".."}
    ):
        return None
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        return None
    return owner, repo, number


def _unavailable_pull_request(record: object) -> dict[str, Any]:
    full_name = record.get("repo_full_name") if isinstance(record, Mapping) else None
    number = record.get("number") if isinstance(record, Mapping) else None
    return {
        "repoFullName": full_name if isinstance(full_name, str) else None,
        "number": number if isinstance(number, int) and not isinstance(number, bool) else None,
        "url": None,
        "statusAvailable": False,
        "state": None,
        "isDraft": None,
        "mergeConflictState": None,
        "checksAvailable": False,
        "failingChecks": [],
        "pendingCheckCount": None,
        "inconclusiveCheckCount": None,
        "commentsAvailable": False,
        "unresolvedReviewThreadCount": None,
        "unresolvedReviewThreads": [],
    }


def _normalize_checks(
    runs: list[dict[str, Any]], statuses: list[dict[str, Any]]
) -> tuple[list[dict[str, str | None]], int, int]:
    failing: list[dict[str, str | None]] = []
    pending = 0
    inconclusive = 0
    for run in runs:
        if run.get("name") == APPROVAL_CHECK_RUN_NAME:
            continue
        status = run.get("status")
        conclusion = run.get("conclusion")
        if status != "completed":
            pending += 1
        elif conclusion in _FAILING_CHECK_CONCLUSIONS:
            failing.append(
                {
                    "name": run.get("name") if isinstance(run.get("name"), str) else "",
                    "conclusion": conclusion if isinstance(conclusion, str) else None,
                    "url": (
                        run.get("details_url")
                        if isinstance(run.get("details_url"), str)
                        else run.get("html_url")
                        if isinstance(run.get("html_url"), str)
                        else None
                    ),
                }
            )
        elif conclusion in _INCONCLUSIVE_CHECK_CONCLUSIONS:
            inconclusive += 1
    for status in statuses:
        state = status.get("state")
        if state == "pending":
            pending += 1
        elif state in {"failure", "error"}:
            failing.append(
                {
                    "name": (
                        status.get("context") if isinstance(status.get("context"), str) else ""
                    ),
                    "conclusion": state,
                    "url": status.get("target_url")
                    if isinstance(status.get("target_url"), str)
                    else None,
                }
            )
    return failing, pending, inconclusive


def _thread_reply(comment: dict[str, Any]) -> dict[str, Any]:
    author = comment.get("author")
    return {
        "author": author.get("login")
        if isinstance(author, dict) and isinstance(author.get("login"), str)
        else None,
        "body": comment.get("body") if isinstance(comment.get("body"), str) else "",
        "url": comment.get("url") if isinstance(comment.get("url"), str) else None,
    }


@dataclass(frozen=True, slots=True)
class Mergeability:
    """GitHub's verdict on whether a pull request can merge."""

    mergeable: bool | None
    merge_state: str

    @classmethod
    def of(cls, pull: Mapping[str, object]) -> Self:
        """From a GraphQL pull request's ``mergeable`` and ``mergeStateStatus``."""
        mergeable = pull.get("mergeable")
        merge_state = pull.get("mergeStateStatus")
        return cls(
            mergeable={"MERGEABLE": True, "CONFLICTING": False}.get(
                mergeable if isinstance(mergeable, str) else ""
            ),
            merge_state=merge_state.lower() if isinstance(merge_state, str) else "",
        )

    @property
    def conflict_state(self) -> Literal["conflicting", "mergeable", "unknown"]:
        """Only a confirmed clean merge counts as mergeable."""
        if self.mergeable is False or self.merge_state == "dirty":
            return "conflicting"
        if self.mergeable is True and self.merge_state == "clean":
            return "mergeable"
        return "unknown"


_GraphqlPullState = Literal["OPEN", "CLOSED", "MERGED"]
_PULL_REQUEST_STATES: Mapping[_GraphqlPullState, PullRequestState] = {
    "OPEN": "open",
    "CLOSED": "closed",
    "MERGED": "merged",
}


class _CIPullCommit(GraphqlNode):
    commit: dict[str, object]


class _CIPullCommits(GraphqlNode):
    nodes: list[_CIPullCommit]


class _CIPull(GraphqlNode):
    state: _GraphqlPullState
    head_ref_oid: str
    head_ref_name: str
    base_ref_name: str
    commits: _CIPullCommits


class _RestPullCI(BaseModel):
    """The fields of a REST pull request that ``PullRequestCI`` holds."""

    state: str
    merged: bool = False
    head_sha: str = Field(validation_alias=AliasPath("head", "sha"))
    head_ref: str = Field("", validation_alias=AliasPath("head", "ref"))
    base_ref: str = Field("", validation_alias=AliasPath("base", "ref"))


@dataclass(frozen=True, slots=True)
class PullRequestCI:
    """A pull request's state and branches, and the CI on its head commit."""

    state: Literal["open", "closed"]
    merged: bool
    head_sha: str
    head_ref: str
    base_ref: str
    checks: CommitCI


@dataclass(frozen=True, slots=True)
class StandingApprovals:
    """A pull request's head and the commits Robin's standing approvals of it are of.

    A ``None`` commit is one GitHub no longer names, or the unread rest past the first page.
    """

    head: str
    open_swe_commits: tuple[str | None, ...]

    def any_besides(self, commit: str | None) -> bool:
        """Whether Robin approves anything but ``commit``; ``None`` spares nothing."""
        return any(approved is None or approved != commit for approved in self.open_swe_commits)


class _HeadCheckRun(GraphqlNode):
    database_id: int
    name: str
    conclusion: str | None


class _HeadCheckRuns(GraphqlNode):
    page_info: GraphqlPageInfo
    nodes: list[_HeadCheckRun]


class _HeadWorkflow(GraphqlNode):
    database_id: int | None


class _HeadWorkflowRun(GraphqlNode):
    workflow: _HeadWorkflow


class _HeadCheckSuite(GraphqlNode):
    database_id: int
    app: GraphqlCheckApp | None
    workflow_run: _HeadWorkflowRun | None
    check_runs: _HeadCheckRuns

    def runs(self) -> list[dict[str, object]]:
        """The suite's runs as REST lists a commit's check runs, in the fields read here."""
        app = self.app.rest() if self.app else None
        return [
            {
                "id": run.database_id,
                "name": run.name,
                "conclusion": run.conclusion.lower() if run.conclusion else None,
                "app": app,
                "check_suite": {"id": self.database_id},
            }
            for run in self.check_runs.nodes
        ]


class _HeadCheckSuites(GraphqlNode):
    page_info: GraphqlPageInfo
    nodes: list[_HeadCheckSuite]


class _HeadCommit(GraphqlNode):
    oid: str
    check_suites: _HeadCheckSuites


class _HeadCommitNode(GraphqlNode):
    commit: _HeadCommit


class _HeadCommits(GraphqlNode):
    nodes: list[_HeadCommitNode]


class _HeadPull(GraphqlNode):
    state: _GraphqlPullState
    head_ref_oid: str
    commits: _HeadCommits


@dataclass(frozen=True, slots=True)
class HeadChecks:
    """A pull request's state and head commit, with every check run on that commit,
    superseded attempts included.

    ``runs`` take REST's shape (``id``, ``name``, lower-case ``conclusion``, ``app``,
    ``check_suite``); ``workflows`` names the Actions workflow behind each check suite, by
    suite id. ``runs`` is ``None`` when a suite holds more runs than one read returns, or
    GitHub withheld a suite's workflow.
    """

    state: PullRequestState
    sha: str
    runs: list[dict[str, object]] | None
    workflows: dict[int, int]


class _CursorLoop(ValueError):
    """GitHub handed back a page cursor it had already given."""


def _graphql_pull(data: Mapping[str, object]) -> Mapping[str, object] | None:
    repository = data.get("repository")
    pull = repository.get("pullRequest") if isinstance(repository, Mapping) else None
    return pull if isinstance(pull, Mapping) else None


def _next_cursor(connection: Mapping[str, object], seen: set[str]) -> str | None:
    """The cursor of the next page, ``None`` on the last page."""
    page_info = connection.get("pageInfo")
    if not isinstance(page_info, Mapping) or page_info.get("hasNextPage") is not True:
        return None
    cursor = page_info.get("endCursor")
    if not isinstance(cursor, str) or not cursor or cursor in seen:
        raise _CursorLoop
    seen.add(cursor)
    return cursor


@dataclass(frozen=True, slots=True)
class _StatusRead:
    """One answer to ``_STATUS_QUERY``, in the shapes the REST reads it replaces gave.

    ``unreadable`` names the pull request's fields GitHub answered with an error;
    those come back null beside the fields it could read.
    """

    pull: Mapping[str, object]
    unreadable: frozenset[str]

    @classmethod
    def parse(cls, payload: object) -> Self:
        if not isinstance(payload, Mapping):
            raise ValueError("GitHub answered GraphQL without an object")
        errors = payload.get("errors") or []
        unreadable: set[str] = set()
        for error in errors if isinstance(errors, list) else [errors]:
            # A suite whose workflow GitHub withheld keeps its runs, as of an unknown
            # workflow, as when REST could not list Actions runs.
            if GraphQLError.at_workflow_run(error):
                continue
            path = error.get("path") if isinstance(error, Mapping) else None
            if not isinstance(path, list) or len(path) < 3:
                raise GraphQLError(errors)
            unreadable.add(str(path[2]))
        data = payload.get("data")
        pull = _graphql_pull(data) if isinstance(data, Mapping) else None
        if pull is None:
            raise GraphQLError(errors) if errors else ValueError("GitHub answered no pull request")
        return cls(pull, frozenset(unreadable))

    @staticmethod
    def login(actor: object) -> str | None:
        """An actor's login as REST spells it, where an app's bot ends in ``[bot]``."""
        if not isinstance(actor, Mapping) or not isinstance(login := actor.get("login"), str):
            return None
        return f"{login}[bot]" if actor.get("__typename") == "Bot" else login

    def readable(self, field: str) -> bool:
        return field not in self.unreadable

    def _connection(self, field: str) -> Mapping[str, object] | None:
        connection = self.pull.get(field) if self.readable(field) else None
        return connection if isinstance(connection, Mapping) else None

    @property
    def state(self) -> PullRequestState | None:
        state = self.pull.get("state")
        return _PULL_REQUEST_STATES.get(state) if isinstance(state, str) else None

    @property
    def head_sha(self) -> str | None:
        sha = self.pull.get("headRefOid")
        return sha if isinstance(sha, str) and _SHA_PATTERN.fullmatch(sha) else None

    @property
    def reviews(self) -> tuple[list[object], str | None] | None:
        """The first page of review nodes and the cursor after it, if any."""
        reviews = self._connection("reviews")
        if reviews is None or not isinstance(nodes := reviews.get("nodes"), list):
            return None
        try:
            cursor = _next_cursor(reviews, set())
        except _CursorLoop:
            return None
        return list(nodes), cursor

    @classmethod
    def reviewers_of(cls, reviews: Sequence[object]) -> list[PullRequestReviewer]:
        """Each reviewer's standing review among GraphQL ``reviews`` nodes."""
        parsed: list[_GithubReview] = []
        for node in reviews:
            if not isinstance(node, Mapping):
                continue
            author = node.get("author")
            login = cls.login(author)
            user_id = author.get("databaseId") if isinstance(author, Mapping) else None
            parsed.append(
                _GithubReview.model_validate(
                    {
                        "id": node.get("fullDatabaseId"),
                        "state": node.get("state"),
                        "user": {
                            "id": user_id,
                            "login": login,
                            "avatar_url": author.get("avatarUrl"),
                        }
                        if isinstance(author, Mapping) and login and isinstance(user_id, int)
                        else None,
                    }
                )
            )
        return PullRequestReviewer.latest(parsed)

    @property
    def review_threads(self) -> tuple[list[dict[str, object]], str | None] | None:
        """The first page of unresolved review threads and the cursor after it, if any."""
        threads = self._connection("reviewThreads")
        if threads is None or not isinstance(nodes := threads.get("nodes"), list):
            return None
        try:
            cursor = _next_cursor(threads, set())
        except _CursorLoop:
            return None
        return PullRequestClient.unresolved_in(nodes), cursor

    def checks(self, sha: str) -> CommitCI | None:
        """The latest check runs and statuses on ``sha``.

        ``None`` when they were unreadable, not ``sha``'s, or ran past one page.
        """
        commits = self._connection("commits")
        nodes = commits.get("nodes") if commits is not None else None
        if not isinstance(nodes, list):
            return None
        commit = nodes[-1].get("commit") if nodes and isinstance(nodes[-1], Mapping) else None
        if not isinstance(commit, Mapping) or commit.get("oid") != sha:
            return None
        try:
            return CommitCI.from_graphql(commit)
        except ValidationError:
            logger.warning("Unreadable head commit checks", extra={"sha": sha}, exc_info=True)
            return None


@dataclass(frozen=True, slots=True)
class PullRequestClient:
    """One pull request's calls on GitHub; plain reads and writes raise like ``RepoClient``.

    The status reads (``mergeability``, ``unresolved_threads``, ``tips``) and the statuses
    built from them answer ``None`` or an unknown instead, so one failed read degrades its
    part of a status rather than the whole of it.
    """

    repo: RepoClient
    number: int

    @classmethod
    @asynccontextmanager
    async def as_app(cls, owner: str, repo: str, number: int) -> AsyncIterator[Self]:
        """This pull request as the Open SWE GitHub App's installation on ``owner/repo`` reads it.

        Raises ``GitHubAppUnavailable`` when no installation token can be minted.
        """
        async with GitHubClient.as_app(owner, repo) as github:
            yield cls(github.repo(owner, repo), number)

    @classmethod
    def of(cls, github: GitHubClient, record: object) -> Self | None:
        """The client for a ``repo_full_name`` and ``number``, or for a search hit's ``repository_url``."""
        if not isinstance(record, Mapping):
            return None
        full_name = record.get("repo_full_name")
        repository_url = record.get("repository_url")
        prefix = f"{GITHUB_API_BASE}/repos/"
        if not isinstance(full_name, str):
            full_name = (
                repository_url.removeprefix(prefix)
                if isinstance(repository_url, str) and repository_url.startswith(prefix)
                else ""
            )
        identity = pull_request_identity(
            {"repo_full_name": full_name, "number": record.get("number")}
        )
        if identity is None:
            return None
        owner, name, number = identity
        return cls(github.repo(owner, name), number)

    @property
    def _log_extra(self) -> dict[str, object]:
        return {"pr_repo_full_name": self.repo.full_name, "pr_number": self.number}

    async def pull(self, *, rendered_body: bool = False) -> dict[str, Any]:
        payload = (
            (
                await self.repo.github.request(
                    "GET",
                    f"repos/{self.repo.full_name}/pulls/{self.number}",
                    headers={"Accept": "application/vnd.github.full+json"},
                )
            ).json()
            if rendered_body
            else await self.repo.get(f"pulls/{self.number}")
        )
        if not isinstance(payload, dict):
            raise ValueError("GitHub answered the pull request without an object")
        return payload

    async def head_checks(self) -> HeadChecks:
        """The pull request's state and the check runs on its head, read over GraphQL so
        they spend none of the REST quota. A suite's app GitHub withholds, as for another
        organization behind an IP allow list, reads as unknown; a withheld workflow gives
        no runs, since a failed redeploy can't be told from the success before it without
        one. Raises like ``RepoClient``; ``ValueError`` when the head moves between pages."""
        runs: list[dict[str, object]] = []
        workflows: dict[int, int] = {}
        cursor: str | None = None
        seen: set[str] = set()
        sha: str | None = None
        while True:
            data, workflows_withheld = await self.repo.ci_graphql(
                _HEAD_CHECKS_QUERY, {"number": self.number, "cursor": cursor}
            )
            pull = _HeadPull.model_validate(_graphql_pull(data))
            commit = pull.commits.nodes[-1].commit if pull.commits.nodes else None
            if commit is None or commit.oid != pull.head_ref_oid or sha not in {None, commit.oid}:
                raise ValueError("The pull request's head moved while its checks were read")
            sha = commit.oid
            if workflows_withheld:
                return HeadChecks(_PULL_REQUEST_STATES[pull.state], sha, None, {})
            suites = commit.check_suites
            for suite in suites.nodes:
                if suite.check_runs.page_info.has_next_page:
                    return HeadChecks(_PULL_REQUEST_STATES[pull.state], sha, None, {})
                runs += suite.runs()
                workflow = suite.workflow_run.workflow.database_id if suite.workflow_run else None
                if workflow is not None:
                    workflows[suite.database_id] = workflow
            if not suites.page_info.has_next_page:
                return HeadChecks(_PULL_REQUEST_STATES[pull.state], sha, runs, workflows)
            cursor = suites.page_info.end_cursor
            if not cursor or cursor in seen:
                raise _CursorLoop
            seen.add(cursor)

    async def update_title(self, title: str) -> None:
        await self.repo.patch(f"pulls/{self.number}", {"title": title})

    async def update_description(self, body: str) -> None:
        await self.repo.patch(f"pulls/{self.number}", {"body": body})

    async def head_sha(self) -> str:
        head = (await self.pull()).get("head")
        sha = head.get("sha") if isinstance(head, dict) else None
        if not isinstance(sha, str) or not sha:
            raise ValueError("GitHub answered the pull request without a head commit")
        return sha

    async def ci(self) -> PullRequestCI:
        """The pull's state, branches and head commit CI, in one GraphQL query when they fit.

        GraphQL can deny a token what REST grants it, so a denied read is retried over REST.
        """
        try:
            return await self._ci_over_graphql()
        except (GraphQLError, httpx2.HTTPStatusError) as exc:
            if not self._denied(exc):
                raise
            logger.warning(
                "GitHub denied the pull request's CI over GraphQL; reading it over REST",
                extra={
                    **self._log_extra,
                    "graphql_error_paths": exc.paths if isinstance(exc, GraphQLError) else None,
                },
            )
        pull = _RestPullCI.model_validate(await self.pull())
        return PullRequestCI(
            state="open" if pull.state == "open" else "closed",
            merged=pull.merged,
            head_sha=pull.head_sha,
            head_ref=pull.head_ref,
            base_ref=pull.base_ref,
            checks=await self.repo.commit_ci_over_rest(pull.head_sha),
        )

    @staticmethod
    def _denied(exc: GraphQLError | httpx2.HTTPStatusError) -> bool:
        """Whether GitHub refused ``exc``'s read for access, not for its rate limit."""
        if isinstance(exc, GraphQLError):
            return "FORBIDDEN" in exc.types
        response = exc.response
        return response.status_code in {401, 403} and not GitHubError(response).rate_limited

    async def _ci_over_graphql(self) -> PullRequestCI:
        data, _ = await self.repo.ci_graphql(_PULL_CI_QUERY, {"number": self.number})
        pull = _graphql_pull(data)
        if pull is None:
            raise ValueError("GitHub answered without the pull request")
        parsed = _CIPull.model_validate(pull)
        head = parsed.commits.nodes[-1].commit if parsed.commits.nodes else None
        if head is not None and head.get("oid") == parsed.head_ref_oid:
            checks = CommitCI.from_graphql(head) or await self.repo.commit_ci_over_rest(
                parsed.head_ref_oid
            )
        else:
            checks = await self.repo.commit_ci(parsed.head_ref_oid)
        return PullRequestCI(
            state="open" if parsed.state == "OPEN" else "closed",
            merged=parsed.state == "MERGED",
            head_sha=parsed.head_ref_oid,
            head_ref=parsed.head_ref_name,
            base_ref=parsed.base_ref_name,
            checks=checks,
        )

    async def merge_base(self, head_sha: str) -> str:
        """Where ``head_sha`` meets the base branch: as it is now while the pull is open.

        An open pull's ``base.sha`` can predate ``head_sha``: read right after a push
        that merged the base branch in, it still names the older base. A closed pull's
        base branch may already contain ``head_sha``, so its last ``base.sha`` is used.
        """
        pull = await self.pull()
        base = pull.get("base")
        ref, sha = (base.get("ref"), base.get("sha")) if isinstance(base, dict) else (None, None)
        against = ref if pull.get("state") == "open" else sha
        if not isinstance(against, str) or not against:
            raise ValueError("GitHub answered the pull request without its base")
        return await self.repo.merge_base(against, head_sha)

    async def tips(self) -> PullRequestTips | None:
        """The base and head commits as GitHub has them now, or ``None`` when unreadable.

        Read over GraphQL, whose quota is separate from the REST one the file list spends.
        """
        try:
            pull = _graphql_pull(await self.repo.graphql(_TIPS_QUERY, {"number": self.number}))
        except httpx2.HTTPError, ValueError, GraphQLError:
            logger.warning(
                "Could not read the pull request's tips", extra=self._log_extra, exc_info=True
            )
            return None
        base_sha = pull.get("baseRefOid") if pull is not None else None
        head_sha = pull.get("headRefOid") if pull is not None else None
        if not isinstance(base_sha, str) or not base_sha:
            return None
        if not isinstance(head_sha, str) or not head_sha:
            return None
        return PullRequestTips(base_sha, head_sha)

    async def files(self, *, max_pages: int = 1, count: int | None = None) -> FileList:
        """Changed files, up to ``max_pages`` pages of 100.

        ``count``, the pull's ``changed_files``, lets those pages load at once.
        A list already read at the same base and head commits is served without
        asking GitHub; a retarget or a push changes them, so the list is read again.
        """
        tips = await self.tips()
        if tips is None:
            return await self._listed_files(max_pages=max_pages, count=count)
        return await CHANGED_FILES.read(
            self.repo.full_name,
            self.number,
            tips,
            max_pages,
            partial(self._listed_files, max_pages=max_pages, count=count),
            self._confirm_tips,
        )

    async def _confirm_tips(self) -> PullRequestTips | None:
        """Confirm with an HTTP lifetime independent of the caller's context."""
        async with self.repo.github.fork() as github:
            return (
                await github.repo(self.repo.owner, self.repo.name).pull_request(self.number).tips()
            )

    async def _listed_files(self, *, max_pages: int, count: int | None) -> FileList:
        """Validate the GitHub file payload without dropping fields."""
        return _FILE_LIST.validate_python(
            await self.repo.pages(f"pulls/{self.number}/files", max_pages=max_pages, total=count)
        )

    async def issue_comments(self) -> list[dict[str, Any]]:
        """Top-level conversation comments."""
        return await self.repo.pages(f"issues/{self.number}/comments")

    async def comment(self, body: str) -> object:
        """Add a top-level conversation comment."""
        return await self.repo.post(f"issues/{self.number}/comments", {"body": body})

    async def edit_comment(self, comment_id: int, body: str) -> object:
        """Replace the body of one of this pull request's top-level conversation comments."""
        return await self.repo.patch(f"issues/comments/{comment_id}", {"body": body})

    async def labels(self) -> list[dict[str, Any]]:
        return await self.repo.pages(f"issues/{self.number}/labels")

    async def add_label(self, name: str) -> None:
        await self.repo.post(f"issues/{self.number}/labels", {"labels": [name]})

    async def remove_label(self, name: str) -> None:
        await self.repo.delete(f"issues/{self.number}/labels/{quote(name, safe='')}")

    async def review_comments(
        self, *, newest_first: bool = False, max_pages: int | None = None
    ) -> list[dict[str, Any]]:
        """Inline review comments, oldest first unless ``newest_first``."""
        params = {"sort": "created", "direction": "desc"} if newest_first else None
        return await self.repo.pages(
            f"pulls/{self.number}/comments", params=params, max_pages=max_pages
        )

    async def reply_to_review_comment(self, comment_id: int, body: str) -> object:
        """Reply in the inline thread that ``comment_id`` opened."""
        return await self.repo.post(
            f"pulls/{self.number}/comments/{comment_id}/replies", {"body": body}
        )

    async def commits(self) -> list[dict[str, Any]]:
        return await self.repo.pages(f"pulls/{self.number}/commits")

    async def contribution_commits(self, base_sha: str) -> list[dict[str, object]]:
        """PR commits excluding merges whose incoming parents are already on the base."""
        if not base_sha:
            raise ValueError("Missing base SHA for contributor evaluation")
        contributions: list[dict[str, object]] = []
        for commit in await self.commits():
            parents = commit.get("parents")
            if isinstance(parents, list) and len(parents) > 1:
                incoming: list[str] = []
                for parent in parents[1:]:
                    sha = parent.get("sha") if isinstance(parent, dict) else None
                    if not isinstance(sha, str) or not sha:
                        raise ValueError("Missing merge parent SHA for contributor evaluation")
                    incoming.append(sha)
                for sha in incoming:
                    if await self.repo.merge_base(sha, base_sha) != sha:
                        break
                else:
                    continue
            contributions.append(commit)
        return contributions

    async def add_review_comment(self, comment: Mapping[str, object]) -> object:
        """Post one inline comment now, outside any review."""
        return await self.repo.post(f"pulls/{self.number}/comments", comment)

    async def create_review(self, review: Mapping[str, object]) -> object:
        """Start a review; without an ``event`` it stays pending for its author."""
        return await self.repo.post(f"pulls/{self.number}/reviews", review)

    async def submit_review(self, review_id: int, *, event: str, body: str) -> object:
        payload: dict[str, object] = {"event": event}
        if body:
            payload["body"] = body
        return await self.repo.post(f"pulls/{self.number}/reviews/{review_id}/events", payload)

    async def delete_review(self, review_id: int) -> None:
        await self.repo.delete(f"pulls/{self.number}/reviews/{review_id}")

    async def review_threads(self, *, roots_only: bool = False) -> list[dict[str, Any]]:
        """Every review thread, optionally with only its root comment's identity."""
        query = _REVIEW_THREAD_ROOTS_QUERY if roots_only else _ALL_REVIEW_THREADS_QUERY
        threads: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            pull = _graphql_pull(
                await self.repo.graphql(query, {"number": self.number, "cursor": cursor})
            )
            connection = pull.get("reviewThreads") if pull is not None else None
            if not isinstance(connection, Mapping) or not isinstance(connection.get("nodes"), list):
                raise ValueError("GitHub answered review threads without a list")
            threads.extend(node for node in connection["nodes"] if isinstance(node, dict))
            cursor = _next_cursor(connection, seen)
            if cursor is None:
                return threads

    async def review_thread_states(self) -> dict[str, bool]:
        """Explicit resolution states, returned only after every page was read."""
        states: dict[str, bool] = {}
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            pull = _graphql_pull(
                await self.repo.graphql(
                    _REVIEW_THREAD_STATES_QUERY, {"number": self.number, "cursor": cursor}
                )
            )
            connection = pull.get("reviewThreads") if pull is not None else None
            if not isinstance(connection, Mapping) or not isinstance(connection.get("nodes"), list):
                raise ValueError("GitHub answered finding threads without a list")
            page_info = connection.get("pageInfo")
            if not isinstance(page_info, Mapping) or not isinstance(
                page_info.get("hasNextPage"), bool
            ):
                raise ValueError("GitHub answered finding threads without pagination")
            for node in connection["nodes"]:
                if (
                    not isinstance(node, Mapping)
                    or not isinstance(thread_id := node.get("id"), str)
                    or not thread_id
                    or not isinstance(resolved := node.get("isResolved"), bool)
                    or thread_id in states
                ):
                    raise ValueError("GitHub answered an invalid finding thread")
                states[thread_id] = resolved
            cursor = _next_cursor(connection, seen)
            if cursor is None:
                return states

    async def add_review_thread(self, thread: Mapping[str, object]) -> None:
        """Add an inline comment to a pending review; ``thread`` is GraphQL's thread input."""
        await self.repo.github.graphql(_ADD_REVIEW_THREAD_MUTATION, {"input": dict(thread)})

    async def set_thread_resolved(self, thread_node_id: str, resolved: bool) -> None:
        await self.repo.github.graphql(
            _RESOLVE_THREAD_MUTATION if resolved else _UNRESOLVE_THREAD_MUTATION,
            {"id": thread_node_id},
        )

    async def edit_pending_comment(self, comment_node_id: str, body: str) -> None:
        """Edit a comment in a pending review, which REST cannot reach yet."""
        await self.repo.github.graphql(
            _UPDATE_REVIEW_COMMENT_MUTATION,
            {"input": {"pullRequestReviewCommentId": comment_node_id, "body": body}},
        )

    async def dismiss_review(self, review_id: int, message: str) -> None:
        await self.repo.github.request(
            "PUT",
            f"repos/{self.repo.full_name}/pulls/{self.number}/reviews/{review_id}/dismissals",
            json={"message": message, "event": "DISMISS"},
        )

    async def standing_approvals(self) -> StandingApprovals:
        """The head and Robin's standing approvals, in one GraphQL read."""
        pull = _graphql_pull(
            await self.repo.graphql(_STANDING_APPROVALS_QUERY, {"number": self.number})
        )
        head = pull.get("headRefOid") if pull is not None else None
        reviews = pull.get("reviews") if pull is not None else None
        if (
            not isinstance(head, str)
            or not isinstance(reviews, Mapping)
            or not isinstance(nodes := reviews.get("nodes"), list)
        ):
            raise ValueError("GitHub answered the pull request without its head or approvals")
        commits: list[str | None] = []
        for node in nodes:
            author = node.get("author") if isinstance(node, Mapping) else None
            login = author.get("login") if isinstance(author, Mapping) else None
            if not isinstance(login, str) or login.lower() not in OPEN_SWE_AUTHOR_LOGINS:
                continue
            commit = node.get("commit")
            oid = commit.get("oid") if isinstance(commit, Mapping) else None
            commits.append(oid if isinstance(oid, str) else None)
        page_info = reviews.get("pageInfo")
        if not isinstance(page_info, Mapping) or page_info.get("hasNextPage") is not False:
            commits.append(None)
        return StandingApprovals(head=head, open_swe_commits=tuple(commits))

    async def withdraw_open_swe_approvals(
        self,
        *,
        reason: str,
        keep_commit: str | None = None,
        scope_to_review_id: int | None = None,
        state: Literal["approved", "changes_requested"] = "approved",
    ) -> list[int]:
        """Dismiss Robin's standing reviews in ``state`` except those of ``keep_commit``; the ids GitHub refused.

        ``scope_to_review_id`` limits this to that review's login and the reviews up to it.
        An unknown review dismisses nothing.
        """
        reviews = _GITHUB_REVIEWS.validate_python(await self.reviews())
        only_login: str | None = None
        if scope_to_review_id is not None:
            own_review = next((r for r in reviews if r.id == scope_to_review_id), None)
            if own_review is None or own_review.user is None:
                return []
            only_login = own_review.user.login.lower()
        refused: list[int] = []
        for review in reviews:
            if (
                review.state != state
                or review.user is None
                or review.user.login.lower() not in OPEN_SWE_GITHUB_LOGINS
                or (only_login is not None and review.user.login.lower() != only_login)
                or (scope_to_review_id is not None and review.id > scope_to_review_id)
                or (keep_commit is not None and review.commit_id == keep_commit)
            ):
                continue
            try:
                await self.dismiss_review(review.id, reason)
            except httpx2.HTTPStatusError as exc:
                # A submitted review cannot be deleted, so a 404 hides one that still stands.
                logger.warning(
                    "GitHub refused to dismiss a stale Open SWE approval",
                    extra={
                        **self._log_extra,
                        "github_review_id": review.id,
                        "status_code": exc.response.status_code,
                    },
                    exc_info=True,
                )
                refused.append(review.id)
            except httpx2.HTTPError:
                logger.warning(
                    "Failed to dismiss a stale Open SWE approval",
                    extra={**self._log_extra, "github_review_id": review.id},
                    exc_info=True,
                )
                refused.append(review.id)
        return refused

    async def request_reviewers(self, logins: list[str]) -> None:
        await self.repo.post(f"pulls/{self.number}/requested_reviewers", {"reviewers": logins})

    async def remove_requested_reviewers(self, logins: list[str]) -> None:
        await self.repo.delete(f"pulls/{self.number}/requested_reviewers", {"reviewers": logins})

    async def merge(self, *, sha: str, method: str, commit_message: str | None = None) -> None:
        """Merge ``sha`` with ``method``; GitHub refusing it raises ``GitHubError``."""
        payload: dict[str, object] = {"sha": sha, "merge_method": method}
        if commit_message is not None:
            payload["commit_message"] = commit_message
        await self.repo.github.request(
            "PUT", f"repos/{self.repo.full_name}/pulls/{self.number}/merge", json=payload
        )

    async def enable_auto_merge(
        self,
        *,
        sha: str,
        method: str,
        commit_message: str | None = None,
        queue_only: bool = False,
    ) -> Literal["auto_merge", "queued"]:
        """Enable auto-merge or enter the queue for the head the person inspected.

        This mutation requires repository auto-merge, including for queue entry.
        """
        pull = await self.pull()
        head = pull.get("head")
        if not isinstance(head, Mapping) or head.get("sha") != sha:
            raise HTTPException(409, "The PR head changed. Refresh before enabling auto-merge.")
        node_id = pull.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("GitHub did not return the pull request ID.")
        if queue_only:
            data = await self.repo.github.graphql(
                """
                query MergeQueueRequirement($id: ID!) {
                  node(id: $id) { ... on PullRequest { isMergeQueueEnabled } }
                }
                """,
                {"id": node_id},
            )
            node = data.get("node")
            enabled = node.get("isMergeQueueEnabled") if isinstance(node, Mapping) else None
            if enabled is False:
                raise HTTPException(
                    409, "The merge queue requirement changed. Refresh before merging."
                )
            if enabled is not True:
                raise ValueError("GitHub did not confirm the merge queue requirement.")
        mutation_input: dict[str, object] = {
            "pullRequestId": node_id,
            "expectedHeadOid": sha,
        }
        if not queue_only:
            mutation_input["mergeMethod"] = method.upper()
            if commit_message is not None:
                mutation_input["commitBody"] = commit_message
        data = await self.repo.github.graphql(
            """
            mutation EnableAutoMerge($input: EnablePullRequestAutoMergeInput!) {
              enablePullRequestAutoMerge(input: $input) {
                pullRequest { autoMergeRequest { enabledAt } isInMergeQueue }
              }
            }
            """,
            {"input": mutation_input},
        )
        enabled = data.get("enablePullRequestAutoMerge")
        updated = enabled.get("pullRequest") if isinstance(enabled, Mapping) else None
        if isinstance(updated, Mapping):
            if updated.get("isInMergeQueue") is True:
                return "queued"
            if updated.get("autoMergeRequest"):
                return "auto_merge"
        raise ValueError("GitHub did not confirm auto-merge or queue entry.")

    async def disable_auto_merge(self) -> None:
        """Disable GitHub's auto-merge for this pull request."""
        pull = await self.pull()
        node_id = pull.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("GitHub did not return the pull request ID.")
        data = await self.repo.github.graphql(
            """
            mutation DisableAutoMerge($input: DisablePullRequestAutoMergeInput!) {
              disablePullRequestAutoMerge(input: $input) {
                pullRequest { autoMergeRequest { enabledAt } }
              }
            }
            """,
            {"input": {"pullRequestId": node_id}},
        )
        disabled = data.get("disablePullRequestAutoMerge")
        updated = disabled.get("pullRequest") if isinstance(disabled, Mapping) else None
        if (
            not isinstance(updated, Mapping)
            or "autoMergeRequest" not in updated
            or updated["autoMergeRequest"] is not None
        ):
            raise ValueError("GitHub did not confirm auto-merge was disabled.")

    async def queue_state(self) -> QueuePull:
        """Fail closed on unreadable or changing PR/review snapshots."""
        query = """
        query($owner:String!, $repo:String!, $number:Int!, $cursor:String) {
          repository(owner:$owner, name:$repo) {
            pullRequest(number:$number) {
              number state merged isDraft headRefOid baseRefOid baseRefName reviewDecision
              baseRef { target { oid } }
              headRepository { nameWithOwner } baseRepository { nameWithOwner }
              reviews(first:100, after:$cursor) {
                nodes { databaseId state updatedAt commit { oid } }
                pageInfo { hasNextPage endCursor }
              }
            }
          }
        }
        """
        snapshot: QueuePull | None = None
        reviews: list[Review] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(50):
            data = await self.repo.graphql(query, {"number": self.number, "cursor": cursor})
            pull = _graphql_pull(data)
            if pull is None:
                raise ValueError("PR admission state unavailable")
            current = QueuePull.model_validate({**pull, "reviews": ()})
            if current.number != self.number or (snapshot is not None and snapshot != current):
                raise ValueError("PR changed during admission read")
            snapshot = current
            page = ReviewPage.model_validate(pull.get("reviews"))
            reviews.extend(page.nodes)
            if not page.has_next:
                return snapshot.model_copy(update={"reviews": tuple(reviews)})
            if not page.cursor or page.cursor in seen:
                raise ValueError("Invalid review pagination")
            seen.add(page.cursor)
            cursor = page.cursor
        raise ValueError("PR reviews exceed admission pagination limit")

    async def published_app_results(self, head_sha: str, markers: set[str]) -> frozenset[str]:
        pull = await self.pull()
        head = pull.get("head")
        if pull.get("state") != "open" or not isinstance(head, dict) or head.get("sha") != head_sha:
            raise ValueError("App results no longer match an open pull request's head")
        bodies: list[str] = []
        for page in range(1, 3):
            reviews = await self.repo.get(
                f"pulls/{self.number}/reviews", params={"per_page": "100", "page": str(page)}
            )
            if not isinstance(reviews, list) or len(reviews) > 100:
                raise ValueError("GitHub answered without a page of reviews")
            for review in reviews:
                if not isinstance(review, dict) or not isinstance(review.get("body"), str):
                    raise ValueError("GitHub answered without a review body")
                bodies.append(review["body"])
            if len(reviews) < 100:
                return frozenset(
                    marker for marker in markers if any(marker in body for body in bodies)
                )
        raise ValueError("Cannot verify app publication within the PR review pagination budget")

    async def reviews(self) -> list[dict[str, Any]]:
        return await self.repo.pages(f"pulls/{self.number}/reviews")

    async def title_and_body(self) -> tuple[str, str]:
        """The title and description as written, read over GraphQL to spare the REST quota.

        ``body`` is ``""`` when the pull request has no description.
        """
        pull = _graphql_pull(
            await self.repo.graphql(_TITLE_AND_BODY_QUERY, {"number": self.number})
        )
        if pull is None:
            raise ValueError("GitHub answered without the pull request")
        return _as_str(pull.get("title"), ""), _as_str(pull.get("body"), "")

    async def mergeability(self) -> Mergeability | None:
        """Mergeability over GraphQL.

        REST answers ``mergeable: null`` whenever its cached verdict has expired,
        and only starts recomputing it; GraphQL waits for that computation, so a
        single read usually gets the real answer.
        """
        try:
            data = await self.repo.graphql(_MERGEABILITY_QUERY, {"number": self.number})
        except GraphQLError as exc:
            logger.warning(
                "Mergeability query answered with errors; falling back to what REST reported",
                extra={**self._log_extra, "graphql_errors": exc.errors},
            )
            return None
        except httpx2.HTTPError, ValueError:
            logger.warning(
                "Mergeability query failed; falling back to what REST reported",
                extra=self._log_extra,
                exc_info=True,
            )
            return None
        pull = _graphql_pull(data)
        if pull is None:
            logger.warning(
                "Mergeability query answered without a pull request", extra=self._log_extra
            )
            return None
        return Mergeability.of(pull)

    @staticmethod
    def unresolved_in(threads: Sequence[object]) -> list[dict[str, object]]:
        """The unresolved threads among GraphQL ``reviewThreads`` nodes, root comment first."""
        unresolved: list[dict[str, object]] = []
        for thread in threads:
            if not isinstance(thread, dict) or thread.get("isResolved") is True:
                continue
            comments = thread.get("comments")
            nodes = comments.get("nodes") if isinstance(comments, dict) else None
            comment = (
                nodes[0] if isinstance(nodes, list) and nodes and isinstance(nodes[0], dict) else {}
            )
            author = comment.get("author")
            line = thread.get("line")
            if not isinstance(line, int) or isinstance(line, bool):
                line = thread.get("originalLine")
            unresolved.append(
                {
                    "thread_id": thread.get("id") if isinstance(thread.get("id"), str) else None,
                    "author": author.get("login")
                    if isinstance(author, dict) and isinstance(author.get("login"), str)
                    else None,
                    "body": comment.get("body") if isinstance(comment.get("body"), str) else "",
                    "path": thread.get("path") if isinstance(thread.get("path"), str) else "",
                    "line": line if isinstance(line, int) and not isinstance(line, bool) else None,
                    "url": comment.get("url") if isinstance(comment.get("url"), str) else None,
                    "replies": [
                        _thread_reply(reply)
                        for reply in (nodes[1:] if isinstance(nodes, list) else [])
                        if isinstance(reply, dict)
                    ],
                }
            )
        return unresolved

    async def unresolved_threads(
        self, *, after: str | None = None
    ) -> list[dict[str, object]] | None:
        """Unresolved review threads, from the page after the cursor ``after`` when given."""
        unresolved: list[dict[str, object]] = []
        cursor = after
        seen_cursors: set[str] = {after} if after else set()
        try:
            while True:
                data = await self.repo.graphql(
                    _REVIEW_THREADS_QUERY, {"number": self.number, "cursor": cursor}
                )
                pull = _graphql_pull(data)
                threads = pull.get("reviewThreads") if pull is not None else None
                if not isinstance(threads, dict) or not isinstance(threads.get("nodes"), list):
                    return None
                unresolved.extend(self.unresolved_in(threads["nodes"]))
                cursor = _next_cursor(threads, seen_cursors)
                if cursor is None:
                    return unresolved
        except _CursorLoop, httpx2.HTTPError, ValueError:
            return None

    async def _read_status(self, *, thread_comments: bool = False, ci: bool = True) -> _StatusRead:
        """The status query's answer; without ``ci`` it leaves out the head commit's checks."""
        response = await self.repo.github.request(
            "POST",
            GITHUB_GRAPHQL,
            json={
                "query": _STATUS_QUERY,
                "variables": {
                    "owner": self.repo.owner,
                    "repo": self.repo.name,
                    "number": self.number,
                    "threadComments": thread_comments,
                    "ci": ci,
                },
            },
        )
        return _StatusRead.parse(response.json())

    async def _settled_mergeability(self, read: _StatusRead) -> Mergeability:
        """Wait a little for GitHub to decide whether an open pull request merges.

        GitHub computes mergeability in the background and answers ``UNKNOWN`` until
        it finishes.
        """
        mergeability = Mergeability.of(read.pull)
        for attempt in range(1, _MERGEABILITY_ATTEMPTS):
            if mergeability.mergeable is not None or read.state != "open":
                break
            await asyncio.sleep(_MERGEABILITY_DELAY_SECONDS * attempt)
            mergeability = await self.mergeability() or mergeability
        return mergeability

    async def _head_checks(self, read: _StatusRead, sha: str | None) -> CommitCI | None:
        """``sha``'s check runs and statuses, falling back to REST for incomplete GraphQL reads."""
        if sha is None:
            return None
        return read.checks(sha) or await self._rest_checks(sha)

    async def _rest_checks(self, sha: str | None) -> CommitCI | None:
        """``sha``'s check runs and statuses, read over REST alone."""
        return await or_none(self.repo.commit_ci_over_rest(sha)) if sha is not None else None

    async def _head_rest_checks(self) -> tuple[str | None, CommitCI | None]:
        """The head commit REST names, and its checks read over REST."""
        sha = await or_none(self.head_sha())
        return sha, await self._rest_checks(sha)

    async def _reviewers(self, read: _StatusRead) -> list[PullRequestReviewer] | None:
        """``read``'s reviewers, counting the reviews on the pages after it."""
        if (first := read.reviews) is None:
            return None
        reviews, cursor = first
        seen = {cursor} if cursor else set()
        try:
            while cursor is not None:
                pull = _graphql_pull(
                    await self.repo.graphql(
                        _REVIEWS_QUERY, {"number": self.number, "cursor": cursor}
                    )
                )
                page = pull.get("reviews") if pull is not None else None
                if not isinstance(page, Mapping) or not isinstance(page.get("nodes"), list):
                    raise ValueError("GitHub answered reviews without a list")
                reviews.extend(page["nodes"])
                cursor = _next_cursor(page, seen)
            return _StatusRead.reviewers_of(reviews)
        except _CursorLoop, httpx2.HTTPError, ValueError:
            logger.warning("Unreadable pull request reviews", extra=self._log_extra, exc_info=True)
            return None

    async def _unresolved_threads(self, read: _StatusRead) -> list[dict[str, object]] | None:
        """``read``'s unresolved review threads and those on the pages after it."""
        if (first := read.review_threads) is None:
            return None
        threads, cursor = first
        if cursor is None:
            return threads
        rest = await self.unresolved_threads(after=cursor)
        return None if rest is None else threads + rest

    async def thread_status(self) -> dict[str, Any]:
        """The PR health a dashboard thread shows: state, checks and unresolved threads."""
        result = _unavailable_pull_request(
            {"repo_full_name": self.repo.full_name, "number": self.number}
        )
        result["url"] = f"https://github.com/{self.repo.full_name}/pull/{self.number}"
        # Checks come over REST alone: on a repository with many of them GitHub takes
        # seconds to build the query's one page of them, which then overflows anyway,
        # while REST reads every page at once and mostly answers 304s. They are read
        # alongside the query, for the head commit REST names.
        with MeteredTransport.attributed():
            read, (checked_sha, early_checks) = await asyncio.gather(
                or_none(self._read_status(thread_comments=True, ci=False)),
                self._head_rest_checks(),
            )
        if read is None:
            return result
        state = read.state
        draft = read.pull.get("isDraft")
        result.update(
            {
                "statusAvailable": state is not None and isinstance(draft, bool),
                "state": state,
                "isDraft": draft if isinstance(draft, bool) else None,
                "mergeConflictState": Mergeability.of(read.pull).conflict_state,
            }
        )
        with MeteredTransport.attributed():
            if checked_sha == read.head_sha:
                review_threads, checks = await self._unresolved_threads(read), early_checks
            else:
                # A push between the two reads left the early checks on an older commit.
                review_threads, checks = await asyncio.gather(
                    self._unresolved_threads(read), self._rest_checks(read.head_sha)
                )
        if review_threads is not None:
            result.update(
                {
                    "commentsAvailable": True,
                    "unresolvedReviewThreadCount": len(review_threads),
                    "unresolvedReviewThreads": review_threads,
                }
            )
        if checks is not None:
            failing, pending, inconclusive = _normalize_checks(checks.runs, checks.statuses)
            result.update(
                {
                    "checksAvailable": True,
                    "failingChecks": failing,
                    "pendingCheckCount": pending,
                    "inconclusiveCheckCount": inconclusive,
                }
            )
        return result

    async def load(
        self,
        *,
        details: bool = True,
        listed: Mapping[str, object] | None = None,
        wanted: Collection[PullRequestState] = _ANY_STATE,
    ) -> OpenPullRequest | None:
        """The PR's live status, or ``None`` when its state is not ``wanted``.

        Without ``details`` only ``listed`` (a search hit) is used, and nothing is read;
        a live read that fails falls back to it too.
        """
        status, _ = await self.load_with_ci(details=details, listed=listed, wanted=wanted)
        return status

    async def load_with_ci(
        self,
        *,
        details: bool = True,
        listed: Mapping[str, object] | None = None,
        wanted: Collection[PullRequestState] = _ANY_STATE,
    ) -> tuple[OpenPullRequest | None, CommitCI | None]:
        """``load``'s status, with the head commit's CI it was read from, if any."""
        with MeteredTransport.attributed():
            read = await or_none(self._read_status()) if details else None
        if read is None:
            if "open" not in wanted:
                return None, None
            return (
                OpenPullRequest.listed(
                    self.repo.full_name, self.number, listed or {}, details_loading=not details
                ),
                None,
            )
        state = read.state
        if state is None or state not in wanted:
            return None, None
        pull = read.pull
        mergeability = await self._settled_mergeability(read)
        result = OpenPullRequest(
            repo=self.repo.full_name,
            number=self.number,
            title=_as_str(pull.get("title"), ""),
            state=state,
            created_at=_as_optional_str(pull.get("createdAt")),
            updated_at=_as_optional_str(pull.get("updatedAt")),
            draft=_as_optional_bool(pull.get("isDraft")),
            author=_StatusRead.login(pull.get("author")),
            additions=_as_optional_int(pull.get("additions")),
            deletions=_as_optional_int(pull.get("deletions")),
            mergeable=mergeability.mergeable,
            merge_state=mergeability.merge_state or "unknown",
            auto_merge=bool(pull.get("autoMergeRequest")),
            merge_queue_enabled=pull.get("isMergeQueueEnabled") is True,
            in_merge_queue=pull.get("isInMergeQueue") is True,
            head_ref=_as_optional_str(pull.get("headRefName")),
            status_available=True,
        )
        if result.merge_state == "clean":
            result.required_checks_passing = True
        sha = read.head_sha
        if sha is None:
            return result, None
        result.head_sha = sha
        with MeteredTransport.attributed():
            checks, reviewers, review_threads = await asyncio.gather(
                self._head_checks(read, sha), self._reviewers(read), self._unresolved_threads(read)
            )
        runs, statuses = (checks.runs, checks.statuses) if checks is not None else (None, None)
        result.reviewers = reviewers
        result.review_decision = (
            None if reviewers is None else PullRequestReviewer.decision(reviewers)
        )
        result.review_required = pull.get("reviewDecision") == "REVIEW_REQUIRED"
        result.unresolved_threads = None if review_threads is None else len(review_threads)
        base_ref = _as_optional_str(pull.get("baseRefName"))
        config = None
        if base_ref is not None and (result.unresolved_threads or result.merge_state != "clean"):
            config = await BranchConfig(self.repo.full_name, base_ref).read(self.repo.github.login)
            if config is not None:
                result.conversation_resolution_required = config.requires_conversation_resolution
                # GitHub's reviewDecision is often null under rulesets, so count approvals too.
                if reviewers is not None and config.required_approving_reviews:
                    approvals = sum(reviewer.state == "approved" for reviewer in reviewers)
                    result.review_required |= approvals < config.required_approving_reviews
        if config is not None and runs is not None and result.merge_state != "clean":
            approval_checks = {
                check for check in config.checks if check.name == APPROVAL_CHECK_RUN_NAME
            }
            passing_approvals = [
                run
                for run in runs
                if run.get("name") == APPROVAL_CHECK_RUN_NAME
                and run.get("status") == "completed"
                and run.get("conclusion") == "success"
            ]
            result.review_required |= any(
                not check.reported_by(passing_approvals, []) for check in approval_checks
            )
        if runs is None or statuses is None:
            return result, checks
        ci_runs = [run for run in runs if run.get("name") != APPROVAL_CHECK_RUN_NAME]
        failed, _, _ = _normalize_checks(ci_runs, statuses)
        failures = [_as_str(check["name"], "Unnamed check") for check in failed]
        inconclusive = any(
            run.get("status") == "completed" and run.get("conclusion") in {"cancelled", "stale"}
            for run in ci_runs
        )
        pending = [
            _as_str(run.get("name"), "Unnamed check")
            for run in ci_runs
            if run.get("status") != "completed"
        ] + [
            _as_str(status.get("context"), "Unnamed status")
            for status in statuses
            if status.get("state") == "pending"
        ]
        result.failing_checks = failures
        result.pending_checks = pending
        result.ci = (
            "failing"
            if failures
            else "pending"
            if pending
            else "unknown"
            if inconclusive
            else "passing"
            if ci_runs or statuses
            else "none"
        )
        if config is not None and result.merge_state != "clean":
            required = config.checks
            passing_runs = [
                run
                for run in runs
                if run.get("status") == "completed"
                and run.get("conclusion") in {"success", "neutral", "skipped"}
            ]
            passing_statuses = [status for status in statuses if status.get("state") == "success"]
            result.required_checks_passing = all(
                check.reported_by(passing_runs, passing_statuses) for check in required
            )
            if not pending:
                result.missing_checks = unreported_required_checks(
                    {check for check in required if check.name != APPROVAL_CHECK_RUN_NAME},
                    runs,
                    statuses,
                )
        return result, checks


async def get_pull_request_statuses(
    github: GitHubClient, records: Sequence[object]
) -> list[dict[str, Any]]:
    """Return live status for every tracked pull request record."""
    statuses: list[dict[str, Any]] = []
    for record in records:
        client = PullRequestClient.of(github, record)
        statuses.append(
            await client.thread_status()
            if client is not None
            else _unavailable_pull_request(record)
        )
    return statuses


async def list_open_pull_requests(
    github: GitHubClient,
    login: str,
    repo: str = "",
    *,
    lightweight: bool = False,
    sort: str = "updated",
    direction: str = "desc",
    page: int = 1,
    scope: Literal["mine", "review-requested", "drafts"] = "mine",
) -> OpenPullRequests:
    """Read the open PRs ``login`` authored or is asked to review, with current-head checks."""
    if not _OWNER_PATTERN.fullmatch(login):
        raise HTTPException(422, "invalid GitHub login")
    if not 1 <= page <= _SEARCH_MAX_PAGES:
        raise HTTPException(422, "invalid PR page")
    repositories = list(dict.fromkeys(repo.split(","))) if repo else []
    if any(
        pull_request_identity({"repo_full_name": name, "number": 1}) is None
        for name in repositories
    ):
        raise HTTPException(422, "repository must be owner/repo")
    if sort not in {"created", "updated"} or direction not in {"asc", "desc"}:
        raise HTTPException(422, "invalid PR sort")
    qualifier = "author" if scope in {"mine", "drafts"} else "review-requested"
    query = f"is:pr is:open {qualifier}:{login}"
    if scope == "drafts":
        query += " draft:true"
    for name in repositories:
        query += f" repo:{name}"
    try:
        payload = await github.get(
            "search/issues",
            {
                "q": query,
                "per_page": str(_SEARCH_PAGE_SIZE),
                "page": str(page),
                "sort": sort,
                "order": direction,
            },
        )
    except (httpx2.HTTPError, ValueError) as exc:
        raise HTTPException(502, "Could not load open PRs from GitHub") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise HTTPException(502, "Invalid GitHub PR search response")
    # A timed-out search answers with an arbitrary subset of the matches, so
    # any list built from it would silently hide most of a user's PRs.
    if payload.get("incomplete_results") is True:
        return OpenPullRequests(
            pull_requests=[],
            next_page=None,
            incomplete=True,
            updated_at=datetime.now(UTC).isoformat(),
        )
    semaphore = asyncio.Semaphore(4)

    async def load(item: object) -> OpenPullRequest | None:
        client = PullRequestClient.of(github, item)
        if client is None or not isinstance(item, Mapping):
            return None
        async with semaphore:
            return await client.load(details=not lightweight, listed=item, wanted={"open"})

    with MeteredTransport.attributed():
        items = await asyncio.gather(*(load(item) for item in payload["items"][:_SEARCH_PAGE_SIZE]))
    total = payload.get("total_count")
    has_more = (
        isinstance(total, int) and page * _SEARCH_PAGE_SIZE < total and page < _SEARCH_MAX_PAGES
    )
    return OpenPullRequests(
        pull_requests=[item for item in items if item is not None],
        next_page=page + 1 if has_more else None,
        incomplete=payload.get("incomplete_results") is True,
        updated_at=datetime.now(UTC).isoformat(),
    )
