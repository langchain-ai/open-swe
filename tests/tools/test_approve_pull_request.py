from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid7

import pytest

from openswe.run_config import RunConfig
from openswe.tools.approve_pull_request import ApprovalAuthorization, approve_pull_request
from openswe.transcript.engine import Command, append
from openswe.transcript.events import MessageSender, ThreadCreated, TurnRequested, TurnStarted
from openswe.users import User


async def test_approval_evidence_is_the_latest_requesters_message_in_this_run(
    registry_db: None,
) -> None:
    with patch("openswe.users.models.is_authorized_github_login", AsyncMock(return_value=True)):
        alice = await User.sign_in("github", "101", login="alice")
        bob = await User.sign_in("github", "102", login="bob")
    thread_id, run_id, turn_id = str(uuid7()), str(uuid7()), uuid7()
    quote = "Approve https://github.com/acme/app/pull/7 as me"
    await append(
        thread_id,
        [
            Command(
                command_id="create",
                actor_kind="user",
                event=ThreadCreated(title="Approval", source="slack", owner_login="alice"),
            ),
            Command(
                command_id="request",
                actor_kind="user",
                event=TurnRequested(
                    turn_id=turn_id,
                    message_id="request",
                    text=quote,
                    sender=MessageSender(login=f"user:{alice.id}", kind="slack"),
                ),
            ),
            Command(
                command_id="start",
                actor_kind="system",
                event=TurnStarted(turn_id=turn_id, run_id=run_id),
            ),
        ],
    )
    cfg = RunConfig(thread_id=thread_id, run_id=run_id, github_login="alice", source="slack")
    with patch(
        "openswe.tools.approve_pull_request.pr_author_login", AsyncMock(return_value="alice")
    ):
        authorization = await ApprovalAuthorization.resolve(cfg, quote)
        assert authorization.message_id == "request"
        assert quote in authorization.review_body()
        assert "request" in authorization.review_body()
        for invalid_cfg, evidence in (
            (cfg, "Invented authorization"),
            (cfg.model_copy(update={"run_id": "other-run"}), quote),
            (cfg.model_copy(update={"thread_id": str(uuid7())}), quote),
            (cfg.model_copy(update={"github_login": "bob"}), quote),
            (cfg.model_copy(update={"background_task_completion": True}), quote),
            (cfg.model_copy(update={"source": "github"}), quote),
        ):
            with pytest.raises(ValueError):
                await ApprovalAuthorization.resolve(invalid_cfg, evidence)
        await append(
            thread_id,
            [
                Command(
                    command_id="revoke",
                    actor_kind="user",
                    event=TurnRequested(
                        turn_id=turn_id,
                        message_id="revoke",
                        text="Wait, don't approve",
                        sender=MessageSender(login=f"user:{bob.id}", kind="slack"),
                    ),
                )
            ],
        )
        with pytest.raises(ValueError):
            await ApprovalAuthorization.resolve(cfg, quote)


@pytest.mark.parametrize("condition", ["valid", "self", "draft", "closed", "stale"])
async def test_approval_is_pinned_to_an_open_pr_as_the_requester(condition: str) -> None:
    quote = "Approve https://github.com/acme/app/pull/7 as me"
    authorization = ApprovalAuthorization("alice", "human-1", quote, "thread-1")
    pull = MagicMock()
    pull.pull = AsyncMock(
        return_value={
            "user": {"login": "alice" if condition == "self" else "bob"},
            "draft": condition == "draft",
            "state": "closed" if condition == "closed" else "open",
            "head": {"sha": "changed" if condition == "stale" else "abc123"},
        }
    )
    pull.create_review = AsyncMock(
        return_value={"state": "APPROVED", "html_url": "https://github.com/review"}
    )
    github = MagicMock()
    github.repo.return_value.pull_request.return_value = pull
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=github)
    client.__aexit__ = AsyncMock(return_value=False)
    with (
        patch(
            "openswe.tools.approve_pull_request.RunConfig.from_runtime", return_value=RunConfig()
        ),
        patch(
            "openswe.tools.approve_pull_request.ApprovalAuthorization.resolve",
            AsyncMock(return_value=authorization),
        ),
        patch("openswe.tools.approve_pull_request.require_repo_access_for_user", AsyncMock()),
        patch(
            "openswe.tools.approve_pull_request.GitHubClient.as_user", return_value=client
        ) as as_user,
    ):
        result = await approve_pull_request("https://github.com/acme/app/pull/7", "abc123", quote)
    assert result["success"] is (condition == "valid")
    if condition == "valid":
        as_user.assert_called_once_with("alice")
        submitted = pull.create_review.call_args.args[0]
        assert submitted["event"] == "APPROVE"
        assert submitted["commit_id"] == "abc123"
        assert quote in submitted["body"]
        assert "human-1" in submitted["body"]
    else:
        pull.create_review.assert_not_called()
