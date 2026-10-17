"""Automated pull request review notices a person can reply to on Slack."""

from typing import Literal

from pydantic import BaseModel

NoticeKind = Literal[
    "review_card",
    "expedited_review_card",
    "reviewer_pick",
    "review_snooze_ended",
    "review_reminder",
    "reviewer_released",
    "author_ready_prompt",
    "review_overdue",
]


class ReviewNotice(BaseModel):
    """What a reply is about: the notice, its review request, and the thread changes go to."""

    kind: NoticeKind
    review_request_id: str
    pr_url: str
    text: str = ""
    target_thread_id: str = ""

    def as_data(self) -> dict[str, str]:
        return {key: value for key, value in self.model_dump().items() if value}
