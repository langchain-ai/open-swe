"""The people a dashboard conversation can mention, those involved in it first."""

from collections.abc import Mapping
from typing import Self
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select

from openswe.database import postgres
from openswe.github.pull_requests import PullRequest, ThreadLink
from openswe.human_review.requests import HumanReviewParticipant, HumanReviewRequest
from openswe.review.session import ReviewSessionMetadata
from openswe.users import User
from openswe.utils.thread_participants import participant_ids


class MentionablePerson(BaseModel):
    id: UUID
    name: str
    login: str
    avatar_url: str
    involved: bool


class MentionDirectory(BaseModel):
    people: list[MentionablePerson]

    @classmethod
    async def for_conversation(
        cls, viewer: UUID | None, thread_id: str, metadata: Mapping[str, object]
    ) -> Self:
        """Everyone but ``viewer``: the thread's participants and its PRs' people, then the rest."""
        involved = await cls.involved(thread_id, metadata)
        people = [
            MentionablePerson(
                id=user.id,
                name=user.display_name or user.login_for("github"),
                login=user.github_login,
                avatar_url=user.avatar_url,
                involved=user.id in involved,
            )
            for user in await User.everyone()
            if user.id != viewer
        ]
        people.sort(key=lambda person: (not person.involved, person.name.casefold()))
        return cls(people=people)

    @staticmethod
    async def involved(thread_id: str, metadata: Mapping[str, object]) -> set[UUID]:
        """Who posted in the thread, and who opened, requested review of, or reviewed its PRs."""
        people = {
            user_id
            for identity in participant_ids(metadata)
            if (user_id := User.key_id(identity)) is not None
        }
        if not thread_id or not postgres.configured():
            return people
        async with postgres.session() as session:
            linked = list(
                await session.scalars(
                    select(ThreadLink.pull_request_id).where(ThreadLink.thread_id == thread_id)
                )
            )
            review = ReviewSessionMetadata.parse(metadata)
            if review is not None and (
                pull_request := await PullRequest.get(
                    review.repo_owner, review.repo_name, review.pr_number
                )
            ):
                linked.append(pull_request.id)
            if not linked:
                return people
            rows = await session.scalars(
                select(PullRequest.author_user_id)
                .where(PullRequest.id.in_(linked))
                .union(
                    select(HumanReviewRequest.requested_by_user_id).where(
                        HumanReviewRequest.pull_request_id.in_(linked)
                    ),
                    select(HumanReviewParticipant.user_id)
                    .join(HumanReviewRequest)
                    .where(HumanReviewRequest.pull_request_id.in_(linked)),
                )
            )
            people.update(user_id for user_id in rows if user_id is not None)
        return people
