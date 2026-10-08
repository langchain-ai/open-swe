"""Review assignment decisions Open SWE logs to the event log, as ``human_review.<decision>``."""

from typing import Literal

from pydantic import BaseModel

ReviewDecisionKind = Literal[
    "reviewer_picked",
    "reviewer_joined",
    "reviewers_released",
    "review_overdue",
    "request_closed",
]
ReviewDecisionCause = Literal[
    "",
    # reviewer_joined
    "accepted_pick",
    "signed_up",
    # reviewers_released
    "claimed_by_overlapping_owner",
    "declined",
    "expired",
    "replaced",
    "not_asked",
    "code_owners_approved",
    "approved",
    "merged",
    "closed",
    "dismissed",
    # review_overdue
    "rotation_exhausted",
    "accepted_without_review",
    # request_closed
    "rejected",
    "superseded",
    "cancelled",
]


class ReviewDecision(BaseModel):
    """One decision about a review request; ``reviewers`` are GitHub logins it concerns."""

    decision: ReviewDecisionKind
    review_request_id: str
    pr_url: str
    summary: str
    cause: ReviewDecisionCause = ""
    reviewers: list[str] = []
    code_owners: list[str] = []
    reason: str = ""
