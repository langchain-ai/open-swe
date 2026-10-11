"""Approved PR reminder eligibility."""

import pytest

from openswe.github.merge_reminders import qualifies
from openswe.github.pull_request_status import OpenPullRequest


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, True),
        ({"merge_state": "blocked", "unresolved_threads": 2}, True),
        ({"merge_state": "blocked", "unresolved_threads": 0}, False),
        ({"missing_checks": ["required"]}, False),
        ({"ci": "pending"}, False),
        ({"review_decision": "changes_requested"}, False),
        ({"draft": True}, False),
        ({"mergeable": None}, False),
    ],
)
def test_reminders_require_approval_and_merge_readiness(changes: dict[str, object], expected: bool):
    values: dict[str, object] = {
        "repo": "org/repo",
        "number": 1,
        "title": "Change",
        "status_available": True,
        "draft": False,
        "mergeable": True,
        "merge_state": "clean",
        "review_decision": "approved",
        "ci": "passing",
    }
    assert qualifies(OpenPullRequest.model_validate(values | changes)) is expected
