"""Review cards link to reviews and let picked reviewers answer in Slack."""

import json
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from openswe.github.pull_requests import PullRequest
from openswe.human_review import card
from openswe.human_review.pick_modals import DECLINE_REASONS, SNOOZE_DURATIONS
from openswe.human_review.requests import HumanReviewRequest
from openswe.slack.app_modals import VALUE_LIMIT
from openswe.users import User, UserIdentity


def _request() -> HumanReviewRequest:
    pr = PullRequest(owner="lc", repo="repo", number=7)
    request = HumanReviewRequest(pull_request_id=pr.id, head_sha="abc", kind="standard")
    request.pull_request = pr
    return request


@pytest.mark.parametrize("login", ["robin-agent[bot]", "robin-agent", "open-swe[bot]", "Open-SWE"])
@pytest.mark.parametrize("bot_user_id", ["UBOT", ""])
async def test_robin_authored_card_mentions_slack_bot(
    monkeypatch: pytest.MonkeyPatch, login: str, bot_user_id: str
) -> None:
    monkeypatch.setenv("SLACK_BOT_USER_ID", bot_user_id)
    request = _request()
    request.pull_request.author = login

    text, _ = await card.closed_card(
        request,
        title="Fix",
        author=await request.author_mention(),
        outcome="approved",
        review_states={},
    )

    assert f"authored by {'<@UBOT>' if bot_user_id else '@robin'}" in text
    request.pull_request.author = "ada"
    assert await request.author_mention() == "@ada"


async def test_bot_authored_draft_mentions_linked_author(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    author = User(identities=[UserIdentity(provider="slack", external_id="UADA", login="ada")])
    monkeypatch.setattr(User, "get", AsyncMock(return_value=author))
    request = _request()
    request.kind = "expedited"
    request.awaiting_ready = True
    request.pull_request.author = "robin-agent[bot]"
    request.pull_request.author_user_id = author.id

    assert await request.author_mention() == "<@UADA>"
    assert request.is_author(author.id, "ada")


def test_signing_up_and_accepting_open_the_review() -> None:
    request = _request()

    review, dismiss = card._buttons(request)

    for element in (review, card.accept_button(request)):
        assert element["url"] == request.pull_request.url
        assert json.loads(element["value"])["action"] == "review"
    assert "url" not in dismiss


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
