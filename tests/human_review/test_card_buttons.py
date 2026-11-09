"""Review cards and picks only carry clicks Trigger Server forwards."""

import json
from typing import Any, cast

from openswe.github.pull_requests import PullRequest
from openswe.human_review import card
from openswe.human_review.pick_modals import DECLINE_REASONS, SNOOZE_DURATIONS
from openswe.human_review.requests import HumanReviewRequest
from openswe.slack.app_modals import VALUE_LIMIT


def _request() -> HumanReviewRequest:
    pr = PullRequest(owner="lc", repo="repo", number=7)
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    request.pull_request = pr
    return request


def test_signing_up_and_accepting_are_plain_buttons() -> None:
    request = _request()

    review, dismiss = card._buttons(request)

    for element in (review, dismiss, card.accept_button(request)):
        assert "url" not in element


def test_decline_and_snooze_open_their_modal_through_trigger_server() -> None:
    request = _request()

    for element, name, choices in (
        (card.decline_button(request), "human_review_decline", DECLINE_REASONS),
        (card.snooze_button(request), "human_review_snooze", tuple(SNOOZE_DURATIONS)),
    ):
        assert element["action_id"] == f"mda_modal::{name}:{request.id}"
        assert len(element["value"]) <= VALUE_LIMIT
        view = json.loads(element["value"])
        assert {"callback_id", "private_metadata"}.isdisjoint(view)
        [question] = view["blocks"]
        # Required: Trigger Server answers Slack, so the backend can't send errors back.
        assert not question.get("optional")
        options = cast(list[dict[str, Any]], question["element"]["options"])
        assert tuple(option["value"] for option in options) == choices
