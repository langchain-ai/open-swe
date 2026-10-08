"""One GitHub pull request, its CODEOWNERS, and the reviews people submit, as a scenario boundary."""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

import httpx2

from openswe.dashboard import repo_access
from openswe.expedited_review import eligibility, readiness
from openswe.expedited_review.eligibility import ChangedFile
from openswe.expedited_review.readiness import PullRequestSnapshot, Readiness
from openswe.github import ci, http, org_membership, sdk, token
from openswe.github.codeowners import CodeOwners
from openswe.github.pull_requests import PullRequest
from openswe.github.repositories import Repository
from openswe.human_review import people as review_people
from tests.support.scenarios.core import (
    Fake,
    Person,
    Scenario,
    computing,
    instance_method,
    returning,
)

type ReviewState = Literal["APPROVED", "CHANGES_REQUESTED", "COMMENTED"]


@dataclass
class _Pulls:
    github: GitHub

    async def async_remove_requested_reviewers(
        self, owner: str, repo: str, number: int, *, data: dict[str, list[str]]
    ) -> None:
        self.github.requested.difference_update(data["reviewers"])


@dataclass
class _Rest:
    pulls: _Pulls


@dataclass
class _Client:
    rest: _Rest


@dataclass
class GitHub:
    """The repository ``acme/widgets`` and its pull request #7. Checks are always still running."""

    scenario: Scenario
    author: Person
    files: Sequence[str]
    codeowners: Sequence[str]
    title: str = "Add rate limits to the API"
    owner: str = "acme"
    repo: str = "widgets"
    number: int = 7
    author_user_id: UUID | None = None
    reviews: dict[str, ReviewState] = field(default_factory=dict)
    requested: set[str] = field(default_factory=set)
    _row: PullRequest | None = None

    @property
    def parsed_codeowners(self) -> CodeOwners:
        return CodeOwners.parse("\n".join(self.codeowners) + "\n")

    @property
    def row(self) -> PullRequest:
        """Open SWE's ``pull_request`` row for this pull request, as its code saved it."""
        if self._row is None:
            self._row = PullRequest(
                owner=self.owner,
                repo=self.repo,
                number=self.number,
                author_user_id=self.author_user_id,
            )
        return self._row

    async def _load(self, owner: str, repo: str, number: int) -> PullRequest:
        return self.row

    async def _save(self, row: PullRequest, **_: object) -> PullRequest:
        row.author_user_id = self.author_user_id
        return row

    async def _link_thread(self, row: PullRequest, thread_id: str, **_: object) -> PullRequest:
        return row

    def review(self, person: Person, state: ReviewState) -> None:
        self.reviews[person.login] = state
        self.scenario.record(
            "action",
            f"submits a {state.lower().replace('_', ' ')} review on GitHub",
            source=person.login,
        )

    async def _request(
        self, client: object, method: str, url: str, **kwargs: object
    ) -> httpx2.Response:
        request = httpx2.Request(method, url)
        if url.endswith("/requested_reviewers") and method == "POST":
            body = kwargs.get("json")
            if isinstance(body, dict) and isinstance(reviewers := body.get("reviewers"), list):
                self.requested.update(str(r) for r in reviewers)
            return httpx2.Response(201, json={}, request=request)
        return httpx2.Response(200, json=[], request=request)

    async def _readiness(self, **_: object) -> Readiness:
        snapshot = PullRequestSnapshot(
            state="open",
            merged=False,
            draft=False,
            head_sha="abc",
            title=self.title,
            author=self.author.login,
            mergeable=True,
            mergeable_state="clean",
            check_state="pending",
            unresolved_threads=0,
        )
        return Readiness(snapshot=snapshot, blockers=["checks are still running"])

    @asynccontextmanager
    async def _sdk(self, *_: object) -> AsyncIterator[_Client]:
        yield _Client(_Rest(_Pulls(self)))

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"

    def fakes(self) -> list[tuple[object, Fake]]:
        pr = {
            "number": self.number,
            "title": self.title,
            "body": "",
            "state": "open",
            "draft": False,
            "merged": False,
            "user": {"login": self.author.login, "id": 1},
            "head": {"ref": "feature", "sha": "abc"},
            "base": {"ref": "main"},
        }
        return [
            (review_people.repo_token, returning("token")),
            (token.resolve_github_token, returning(("token", None))),
            (repo_access.assert_repo_access, computing(lambda full_name, _token: str(full_name))),
            (
                eligibility.fetch_changed_files,
                returning([ChangedFile(filename=f) for f in self.files]),
            ),
            (http.github_request, self._request),
            (ci.has_repo_write_permission, returning(True)),
            (ci.fetch_pr, returning(pr)),
            (org_membership.team_members, returning([])),
            (
                readiness.review_authors,
                computing(lambda *_: {login.lower() for login in self.reviews}),
            ),
            (readiness.latest_review_states, computing(lambda *_: dict(self.reviews))),
            (readiness.assess_readiness, self._readiness),
            (sdk.github_sdk, self._sdk),
        ]

    def attribute_fakes(self) -> list[tuple[object, str, object]]:
        return [
            (CodeOwners, "fetch", returning(self.parsed_codeowners)),
            (
                Repository,
                "get",
                returning(Repository(full_name=f"{self.owner}/{self.repo}", default_branch="main")),
            ),
            (PullRequest, "load", self._load),
            (PullRequest, "save", instance_method(self._save)),
            (PullRequest, "link_thread", instance_method(self._link_thread)),
        ]
