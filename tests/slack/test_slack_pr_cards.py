from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

import pytest

from openswe.github.pull_request_status import OpenPullRequest
from openswe.github.pull_requests import PullRequest
from openswe.jobs import queue
from openswe.slack import pr_cards
from openswe.slack.blocks import block_payload
from openswe.slack.pr_cards import (
    CardedMessage,
    CardPlacement,
    CardPull,
    PullRequestCard,
    SlackPullRequestCards,
)
from openswe.webhooks.event_listeners import HEAR_JOB, EventListener
from openswe.webhooks.event_log import LoggedEvent

_URL = "https://github.com/acme/app/pull/{}"


def _card(number: int, title: str = "Add things") -> PullRequestCard:
    pull = CardPull.model_validate(
        {
            "number": number,
            "title": title,
            "state": "open",
            "user": {"login": "alice"},
            "head": {"ref": "feature", "sha": "a" * 40},
            "base": {"ref": "main", "sha": "b" * 40},
            "requested_reviewers": [{"login": "bob"}],
        }
    )
    status = OpenPullRequest(
        repo="acme/app",
        number=number,
        title=title,
        status_available=True,
        ci="failing",
        failing_checks=["lint"],
        reviewers=[],
        unresolved_threads=2,
        mergeable=True,
        merge_state="unstable",
    )
    record = PullRequest(owner="acme", repo="app", number=number)
    return PullRequestCard(record, pull, status, datetime.now(UTC))


def _texts(blocks: list[dict[str, object]]) -> str:
    return str(blocks)


async def test_markers_become_cards_in_place_and_unreadable_ones_stay_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def read(owner: str, repo: str, number: int) -> PullRequestCard:
        if number == 3:
            raise ValueError("no installation")
        return _card(number)

    monkeypatch.setattr(CardedMessage, "_read", staticmethod(read))
    message = (
        f"Opened it:\n{{{{pr-card {_URL.format(1)}}}}}\nAnd the follow-up\n"
        f"{{{{pr-card <{_URL.format(1)}>}}}}\n{{{{pr-card {_URL.format(3)}}}}}"
    )

    carded = await CardedMessage.parse(message)

    assert [card.record.number for _, card in carded.cards] == [1]
    assert carded.unavailable == [_URL.format(3)]
    assert "pr-card" not in carded.text
    blocks = block_payload(carded.blocks(live_until=carded.live_until) or [])
    assert [block["type"] for block in blocks] == ["markdown", "container", "markdown"]
    card = _texts(blocks[1:2])
    assert "*lint* failing" in card
    assert "Waiting on bob" in card
    assert "2 unresolved comments" in card
    assert "Live until" in card


async def test_redraw_replaces_only_its_own_block(monkeypatch: pytest.MonkeyPatch) -> None:
    card_id = uuid7()
    listener = EventListener(
        id=card_id,
        handler=SlackPullRequestCards.name,
        pull_request_id=uuid7(),
        handler_state={"channel_id": "C1", "thread_ts": "1.0", "message_ts": "2.0"},
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    before = block_payload([_card(1, "Old title").block(card_id, live_until=listener.expires_at)])
    other = {"type": "rich_text", "block_id": "prose", "elements": []}
    footer = {"type": "context", "block_id": "open_swe_usage_footer", "elements": []}
    sent: list[list[dict[str, object]]] = []

    async def fetch(_channel: str, _thread: str, _ts: str) -> dict[str, object]:
        return {"text": "fallback", "blocks": [other, *before, footer]}

    async def update(
        _channel: str, _ts: str, text: str, *, blocks: list[dict[str, object]]
    ) -> None:
        assert text == "fallback"
        sent.append(blocks)

    monkeypatch.setattr(pr_cards, "fetch_slack_thread_message_by_ts", fetch)
    monkeypatch.setattr(pr_cards, "update_slack_message", update)

    await SlackPullRequestCards().show(listener, _card(1, "New title"))

    [blocks] = sent
    assert blocks[0] == other and blocks[-1] == footer
    assert "New title" in _texts(blocks) and "Old title" not in _texts(blocks)
    assert len(blocks) == 3
    assert "No longer updating" in _texts(blocks[1:2])


async def _stored_card(number: int) -> PullRequestCard:
    card = _card(number)
    return PullRequestCard(await card.record.ensure(), card.pull, card.status, card.read_at)


def _event(pull_request_id: UUID) -> LoggedEvent:
    return LoggedEvent(
        source="github",
        event_type="check_run.completed",
        delivery_id=str(uuid7()),
        received_at=datetime.now(UTC),
        user_id=None,
        workspace_id=None,
        repository_id=None,
        pull_request_id=pull_request_id,
        payload={},
    )


@pytest.mark.usefixtures("registry_db")
async def test_pull_request_event_redraws_only_live_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    live, expired = await _stored_card(7), await _stored_card(8)
    placement = CardPlacement(channel_id="C1", thread_ts="1.0", message_ts="2.0")
    await SlackPullRequestCards.track(
        [(uuid7(), live)], placement, live_until=now + timedelta(days=1), by=None
    )
    await SlackPullRequestCards.track([(uuid7(), expired)], placement, live_until=now, by=None)

    await EventListener.deliver(_event(live.record.id))
    await EventListener.deliver(_event(expired.record.id))

    hearing = f"{HEAR_JOB}:{SlackPullRequestCards.name}:"
    assert await queue.get(f"{hearing}{live.record.id}") is not None
    assert await queue.get(f"{hearing}{expired.record.id}") is None

    shown: list[int] = []

    async def read(record: PullRequest) -> PullRequestCard:
        return _card(record.number)

    async def show(
        _self: SlackPullRequestCards, listener: EventListener, _drawn: PullRequestCard
    ) -> None:
        shown.append(listener.pull_request.number)

    monkeypatch.setattr(PullRequestCard, "read", staticmethod(read))
    monkeypatch.setattr(SlackPullRequestCards, "show", show)
    assert await EventListener.hear(SlackPullRequestCards.name, live.record.id) == 1
    assert await EventListener.hear(SlackPullRequestCards.name, expired.record.id) == 0
    assert shown == [7]
