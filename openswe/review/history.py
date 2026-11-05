"""A pull request's history on GitHub: its pushes, rewrites, base changes and state changes."""

from datetime import datetime
from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter
from pydantic import BaseModel, Field, TypeAdapter

from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.github.http import GitHubClient
from openswe.github.pull_request_status import PullRequestClient
from openswe.review.conversation import ConversationAuthor, ConversationCommit, expected_shape

router = APIRouter(tags=["review"])

PullRequestStateChange = Literal["draft", "ready", "closed", "reopened", "merged"]


class HistoryForcePush(BaseModel):
    kind: Literal["force_push"] = "force_push"
    ref: Literal["head", "base"]
    author: ConversationAuthor | None
    created_at: datetime
    before: str | None
    after: str | None


class HistoryBaseChange(BaseModel):
    kind: Literal["base_change"] = "base_change"
    author: ConversationAuthor | None
    created_at: datetime
    previous: str
    current: str


class HistoryStateChange(BaseModel):
    kind: Literal["state"] = "state"
    author: ConversationAuthor | None
    created_at: datetime
    state: PullRequestStateChange


HistoryEvent = Annotated[
    ConversationCommit | HistoryForcePush | HistoryBaseChange | HistoryStateChange,
    Field(discriminator="kind"),
]


class _Actor(BaseModel):
    typename: str = Field(alias="__typename")
    login: str
    avatarUrl: str

    @property
    def author(self) -> ConversationAuthor:
        return ConversationAuthor(
            login=self.login, avatar_url=self.avatarUrl, bot=self.typename == "Bot"
        )


class _GitUser(BaseModel):
    login: str
    avatarUrl: str


class _GitActor(BaseModel):
    """A commit's author; ``user`` is null when the email matches no account, as for most bots."""

    name: str | None = None
    avatarUrl: str = ""
    user: _GitUser | None = None

    @property
    def author(self) -> ConversationAuthor | None:
        login = self.user.login if self.user else self.name
        if not login:
            return None
        # GraphQL types a commit's account as a User even for an App, so its login says it's a bot.
        return ConversationAuthor(
            login=login,
            avatar_url=self.user.avatarUrl if self.user else self.avatarUrl,
            bot=login.endswith("[bot]"),
        )


class _Oid(BaseModel):
    oid: str


class _Commit(BaseModel):
    oid: str
    message: str
    committedDate: datetime
    url: str
    author: _GitActor | None = None


class _CommitNode(BaseModel):
    typename: Literal["PullRequestCommit"] = Field(alias="__typename")
    commit: _Commit

    def event(self) -> ConversationCommit:
        return ConversationCommit(
            sha=self.commit.oid,
            author=self.commit.author.author if self.commit.author else None,
            created_at=self.commit.committedDate,
            message=self.commit.message,
            html_url=self.commit.url,
        )


class _ForcePushNode(BaseModel):
    typename: Literal["HeadRefForcePushedEvent", "BaseRefForcePushedEvent"] = Field(
        alias="__typename"
    )
    createdAt: datetime
    actor: _Actor | None = None
    beforeCommit: _Oid | None = None
    afterCommit: _Oid | None = None

    def event(self) -> HistoryForcePush:
        return HistoryForcePush(
            ref="head" if self.typename == "HeadRefForcePushedEvent" else "base",
            author=self.actor.author if self.actor else None,
            created_at=self.createdAt,
            before=self.beforeCommit.oid if self.beforeCommit else None,
            after=self.afterCommit.oid if self.afterCommit else None,
        )


class _BaseChangeNode(BaseModel):
    typename: Literal["BaseRefChangedEvent"] = Field(alias="__typename")
    createdAt: datetime
    actor: _Actor | None = None
    previousRefName: str
    currentRefName: str

    def event(self) -> HistoryBaseChange:
        return HistoryBaseChange(
            author=self.actor.author if self.actor else None,
            created_at=self.createdAt,
            previous=self.previousRefName,
            current=self.currentRefName,
        )


_STATE_CHANGES: dict[str, PullRequestStateChange] = {
    "ConvertToDraftEvent": "draft",
    "ReadyForReviewEvent": "ready",
    "ClosedEvent": "closed",
    "ReopenedEvent": "reopened",
    "MergedEvent": "merged",
}


class _StateNode(BaseModel):
    typename: Literal[
        "ConvertToDraftEvent", "ReadyForReviewEvent", "ClosedEvent", "ReopenedEvent", "MergedEvent"
    ] = Field(alias="__typename")
    createdAt: datetime
    actor: _Actor | None = None

    def event(self) -> HistoryStateChange:
        return HistoryStateChange(
            author=self.actor.author if self.actor else None,
            created_at=self.createdAt,
            state=_STATE_CHANGES[self.typename],
        )


_NODES = TypeAdapter(
    list[
        Annotated[
            _CommitNode | _ForcePushNode | _BaseChangeNode | _StateNode,
            Field(discriminator="typename"),
        ]
    ]
)


class History(BaseModel):
    events: list[HistoryEvent]

    @classmethod
    async def load(cls, pull: PullRequestClient) -> Self:
        with expected_shape(pull):
            nodes = _NODES.validate_python(await pull.history())
        events = [node.event() for node in nodes]
        # GitHub records a merge as a close too; the merge says it.
        merged_at = {
            e.created_at
            for e in events
            if isinstance(e, HistoryStateChange) and e.state == "merged"
        }
        return cls(
            events=[
                e
                for e in events
                if not (
                    isinstance(e, HistoryStateChange)
                    and e.state == "closed"
                    and e.created_at in merged_at
                )
            ]
        )


@router.get("/reviews/{owner}/{repo}/{pr_number}/history")
async def api_get_review_history(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = SESSION_DEP,
) -> History:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    async with GitHubClient.as_user(session["sub"]) as github:
        return await History.load(github.repo(owner, repo).pull_request(pr_number))
