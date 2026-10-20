import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException

from openswe.github.http import GitHubClient
from openswe.github.pull_request_status import PullRequestClient
from openswe.human_review import readiness, standard
from openswe.human_review.requests import HumanReviewRequest
from openswe.slack.blocks import actions, block_payload, button
from openswe.slack.client import GitHubPrRef
from openswe.slack.payloads import SlackButtonValue, SlackInteraction
from openswe.users import User, UserIdentity


@pytest.mark.parametrize("failure", [None, "github", "delivery"])
async def test_draft_request_offers_author_control_without_requesting_review(
    failure: str | None,
) -> None:
    author = User(
        identities=[
            UserIdentity(provider="github", external_id="ada", login="ada"),
            UserIdentity(provider="slack", external_id="U1", login="ada"),
        ]
    )
    pull = MagicMock(spec=PullRequestClient)
    pull.pull = AsyncMock(
        return_value={
            "state": "open",
            "draft": True,
            "user": {"login": "ada"},
            "head": {"sha": "abc"},
        }
    )
    if failure == "github":
        pull.pull.side_effect = HTTPException(502, "GitHub unavailable")
    claimed: set[str] = set()

    async def claim(scope: str, key: str, *, ttl: timedelta) -> bool:
        if key in claimed:
            return False
        claimed.add(key)
        return True

    async def release(scope: str, key: str) -> None:
        claimed.discard(key)

    with (
        patch.object(readiness.event_claims, "claim", AsyncMock(side_effect=claim)),
        patch.object(readiness.event_claims, "release", AsyncMock(side_effect=release)),
        patch.object(HumanReviewRequest, "active_for", AsyncMock(return_value=None)),
        patch.object(standard, "review_blockers", AsyncMock(return_value=["it is a draft"])),
        patch.object(User, "for_login", AsyncMock(return_value=author)),
        patch.object(
            readiness,
            "send_dm_with_location",
            AsyncMock(
                return_value=("D1", "1.2"),
                side_effect=RuntimeError("DM origin unavailable")
                if failure == "delivery"
                else None,
            ),
        ) as dm,
        patch.object(HumanReviewRequest, "save", AsyncMock()) as save,
    ):
        result = await standard._request_review(
            GitHubPrRef(owner="lc", repo="repo", number=7, url="https://github.com/lc/repo/pull/7"),
            pull,
            standard.Origin(thread_id="origin", slack_channel_id="C1", slack_thread_ts="0.1"),
            channel="C2",
            inline_summary="A change",
        )
        if failure is None:
            repeated = await standard._request_review(
                GitHubPrRef(
                    owner="lc", repo="repo", number=7, url="https://github.com/lc/repo/pull/7"
                ),
                pull,
                standard.Origin(thread_id="another-thread"),
                channel="C2",
                inline_summary="The same change",
            )
            assert "already sent or is being delivered" in repeated.error
            assert dm.await_count == 1
        elif failure == "delivery":
            assert not claimed
            dm.side_effect = None
            retry = await readiness.offer_readiness(
                GitHubPrRef(
                    owner="lc", repo="repo", number=7, url="https://github.com/lc/repo/pull/7"
                ),
                pull,
                standard.Origin(thread_id="origin"),
            )
            assert "Sent" in retry
            assert dm.await_count == 2
    assert not result.success
    assert "it is a draft" in result.error
    save.assert_not_awaited()
    if failure is not None:
        assert "Ready for review on GitHub" in result.error
        assert "Do not work around this refusal" in result.error
        return
    assert "author-only Mark ready button" in result.error
    assert dm.await_args is not None
    assert dm.await_args.args[0] == "U1"
    sent = dm.await_args.kwargs["blocks"]
    value = sent[-1]["elements"][0]["value"]
    card = readiness.ReadinessCard.model_validate_json(json.loads(value)["fingerprint"])
    assert card.author == "ada" and card.head_sha == "abc" and card.thread_id == "origin"
    assert len(value) <= 2000


async def test_expired_card_cannot_mark_ready_after_a_replacement_is_sent() -> None:
    card = readiness.ReadinessCard(
        pr_url="https://github.com/lc/repo/pull/7",
        author="ada",
        slack_user_id="U1",
        head_sha="abc",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="expired"):
        await card.mark_ready("U1")


@pytest.mark.parametrize(
    ("change", "actor_login", "allowed"),
    [
        ({}, "ada", True),
        ({}, "grace", False),
        ({"user": {"login": "grace"}}, "ada", False),
        ({"head": {"sha": "new"}}, "ada", False),
        ({"state": "closed"}, "ada", False),
        ({"merged": True}, "ada", False),
    ],
)
async def test_mark_ready_checks_linked_author_and_current_revision(
    change: dict[str, object], actor_login: str, allowed: bool
) -> None:
    card = readiness.ReadinessCard(
        pr_url="https://github.com/lc/repo/pull/7",
        author="ada",
        slack_user_id="U1",
        head_sha="abc",
    )
    actor = User(
        identities=[UserIdentity(provider="github", external_id=actor_login, login=actor_login)]
    )
    pull = MagicMock(spec=PullRequestClient)
    pull.pull = AsyncMock(
        return_value={
            "state": "open",
            "draft": True,
            "user": {"login": "ada"},
            "head": {"sha": "abc"},
            **change,
        }
    )
    github = MagicMock()
    github.repo.return_value.pull_request.return_value = pull
    connection = AsyncMock()
    connection.__aenter__.return_value = github
    with (
        patch.object(User, "for_person", AsyncMock(return_value=actor)),
        patch.object(GitHubClient, "as_user", return_value=connection),
        patch.object(readiness, "act_on_pull_request", AsyncMock()) as mark,
    ):
        if allowed:
            await card.mark_ready("U1")
            mark.assert_awaited_once()
        else:
            with pytest.raises(ValueError):
                await card.mark_ready("U1")
            mark.assert_not_awaited()


@pytest.mark.parametrize(
    "scenario", ["success", "success_web", "github_failure", "forged", "other_bot", "other_user"]
)
async def test_readiness_click_binds_source_and_rolls_back_failed_mutations(scenario: str) -> None:
    card = readiness.ReadinessCard(
        pr_url="https://github.com/lc/repo/pull/7",
        author="ada",
        slack_user_id="U1",
        head_sha="abc",
        thread_id="origin",
        origin_channel="" if scenario == "success_web" else "C1",
        origin_thread="" if scenario == "success_web" else "0.1",
    )
    value = SlackButtonValue.model_validate_json(card.value())
    blocks = block_payload([actions(button("Mark ready", action_id="ready", value=card.value()))])
    source = {
        "ts": "1.2",
        "user": "BOTHER" if scenario == "other_bot" else "B1",
        "text": "Review carefully first",
        "blocks": [] if scenario == "forged" else blocks,
    }
    interaction = SlackInteraction.model_validate(
        {
            "channel": {"id": "D1"},
            "user": {"id": "U2" if scenario == "other_user" else "U1"},
            "message": {"ts": "1.2"},
        }
    )
    updates: list[str] = []

    async def update(*args: object, **kwargs: object) -> None:
        updates.append(str(args[2]))

    async def mark(*args: object) -> None:
        assert updates == ["Marking ready for review…"]
        if scenario == "github_failure":
            raise HTTPException(502, "GitHub unavailable")

    with (
        patch.object(readiness.common, "SLACK_BOT_USER_ID", "B1"),
        patch.object(readiness, "fetch_slack_message_by_ts", AsyncMock(return_value=source)),
        patch.object(readiness, "update_slack_message", AsyncMock(side_effect=update)) as edited,
        patch.object(readiness, "post_slack_ephemeral_message", AsyncMock()) as error,
        patch.object(readiness, "note_for_concierge", AsyncMock()),
        patch.object(readiness.ReadinessCard, "mark_ready", mark),
        patch.object(readiness.event_claims, "release", AsyncMock()),
        patch.object(readiness, "wake_thread_owner", AsyncMock()) as wake,
        patch.object(readiness, "dispatch_agent_run", AsyncMock()) as dispatch,
        patch.object(readiness, "langgraph_client") as client,
    ):
        client.return_value.threads.get = AsyncMock(
            return_value={"metadata": {"source": "web", "github_login": "requester"}}
        )
        tasks = BackgroundTasks()
        await readiness.handle_button(interaction, value, tasks)
        await tasks()
    if scenario in {"success", "success_web"}:
        assert updates[-1].startswith("Marked ready for review:")
        if scenario == "success":
            wake.assert_awaited_once()
        else:
            wake.assert_not_awaited()
            assert dispatch.await_args is not None
            assert dispatch.await_args.args[0] == "origin"
            assert dispatch.await_args.args[2]["github_login"] == "requester"
            assert dispatch.await_args.kwargs["context"]["kind"] == "system"
        error.assert_not_awaited()
    elif scenario == "github_failure":
        assert updates == ["Marking ready for review…", source["text"]]
        assert edited.await_args is not None
        assert edited.await_args.kwargs["blocks"] == blocks
        wake.assert_not_awaited()
        error.assert_awaited_once()
    else:
        assert not updates
        wake.assert_not_awaited()
        error.assert_awaited_once()
