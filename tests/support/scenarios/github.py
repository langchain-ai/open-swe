"""One GitHub pull request, its CODEOWNERS, and the reviews people submit, as a scenario boundary."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

import httpx2

from openswe.dashboard import repo_access
from openswe.expedited_review import readiness
from openswe.expedited_review.eligibility import ChangedFile
from openswe.expedited_review.readiness import PullRequestSnapshot, Readiness
from openswe.github import app, http, org_membership, token
from openswe.github.codeowners import CodeOwners
from openswe.github.pull_requests import PullRequest
from openswe.github.repositories import Repository
from tests.support.scenarios.core import (
    Fake,
    Person,
    Scenario,
    computing,
    instance_method,
    returning,
)

type ReviewState = Literal["APPROVED", "CHANGES_REQUESTED", "COMMENTED"]
GITHUB_REVIEW_REQUEST = "github_review_request"


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
    repo_files: dict[str, str] = field(default_factory=dict)
    code_owner_review_required: bool = False
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
        path = url.split(f"/repos/{self.owner}/{self.repo}/", 1)[-1]
        body = kwargs.get("json")
        reviewers = body.get("reviewers") if isinstance(body, dict) else None
        if path == f"pulls/{self.number}/requested_reviewers" and isinstance(reviewers, list):
            logins = set(map(str, reviewers))
            if method == "DELETE":
                self.requested.difference_update(logins)
                return httpx2.Response(200, json={}, request=request)
            for login in sorted(logins):
                self.requested.add(login)
                # GitHub notifies whoever is requested, so it is a message they get.
                self.scenario.record(
                    "message",
                    "review requested on GitHub",
                    target=login,
                    label=GITHUB_REVIEW_REQUEST,
                )
            return httpx2.Response(201, json={}, request=request)
        if path.startswith("contents/") and method == "GET":
            if (content := self.repo_files.get(path.removeprefix("contents/"))) is None:
                return httpx2.Response(404, json={"message": "Not Found"}, request=request)
            return httpx2.Response(200, text=content, request=request)
        if url.endswith("/graphql"):
            pull = {"reviewDecision": self.review_decision}
            data = {"data": {"repository": {"pullRequest": pull}}}
            return httpx2.Response(200, json=data, request=request)
        if path == f"pulls/{self.number}":
            return httpx2.Response(200, json=self.pull, request=request)
        if path.startswith("collaborators/") and path.endswith("/permission"):
            return httpx2.Response(200, json={"permission": "write"}, request=request)
        return httpx2.Response(200, json=[], request=request)

    @property
    def pull(self) -> dict[str, object]:
        return {
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

    @property
    def review_decision(self) -> str:
        """GitHub's ``reviewDecision``: with code owner review required, every owned file needs
        an approval from one of its owners."""
        approved = {
            f"@{login.lower()}" for login, state in self.reviews.items() if state == "APPROVED"
        }
        if self.code_owner_review_required and any(
            (owners := self.parsed_codeowners.owners_for(path))
            and not approved & {owner.lower() for owner in owners}
            for path in self.files
        ):
            return "REVIEW_REQUIRED"
        return "APPROVED" if approved else "REVIEW_REQUIRED"

    async def _readiness(self, *_: object) -> Readiness:
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

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"

    def fakes(self) -> list[tuple[object, Fake]]:
        return [
            (app.get_github_app_installation_id_for_repo, returning(1)),
            (app.get_github_app_installation_token, returning("token")),
            (token.resolve_github_token, returning(("token", None))),
            (repo_access.assert_repo_access, computing(lambda full_name, _token: str(full_name))),
            (http.github_request, self._request),
            (org_membership.team_members, returning([])),
            (
                readiness.review_authors,
                computing(lambda *_: {login.lower() for login in self.reviews}),
            ),
            (readiness.latest_review_states, computing(lambda *_: dict(self.reviews))),
        ]

    def attribute_fakes(self) -> list[tuple[object, str, object]]:
        return [
            (CodeOwners, "fetch", returning(self.parsed_codeowners)),
            (ChangedFile, "of_pull", returning([ChangedFile(filename=f) for f in self.files])),
            (Readiness, "assess", self._readiness),
            (
                Repository,
                "get",
                returning(Repository(full_name=f"{self.owner}/{self.repo}", default_branch="main")),
            ),
            (PullRequest, "load", self._load),
            (PullRequest, "save", instance_method(self._save)),
            (PullRequest, "link_thread", instance_method(self._link_thread)),
        ]
