from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import JsonValue
from runtime_api.slack import SlackNotice

from openswe.slack import ask as slack_ask
from openswe.slack import routes as slack_routes
from openswe.slack import webhook as slack_service
from openswe.slack.channels import SlackChannel
from openswe.slack.payloads import SlackChannelContext, SlackMessage
from openswe.slack.request import SlackRequest
from openswe.slack.tools import reply as slack_reply
from openswe.tasks import schemas as task_schemas
from openswe.tasks import service as task_service
from openswe.threads.listing import _metadata_matches_filters
from openswe.users import User
from openswe.users.models import UserIdentity


@pytest.fixture
def signed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_routes.common, "verify_slack_signature", lambda **_kwargs: True)
    monkeypatch.setattr(slack_routes.common, "claim_slack_event", AsyncMock(return_value=True))


def _patch_channel(monkeypatch: pytest.MonkeyPatch, messages: list[SlackMessage]) -> None:
    """Point the channel read at ``messages`` without touching Slack."""
    monkeypatch.setattr(SlackChannel, "load", AsyncMock(return_value=SlackChannel(id="C1")))
    monkeypatch.setattr(SlackChannel, "messages", AsyncMock(return_value=messages))


@pytest.fixture
def linked_asker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        slack_ask.common,
        "resolve_slack_channel_context",
        AsyncMock(return_value=SlackChannelContext(name="eng")),
    )
    monkeypatch.setattr(SlackChannelContext, "allows_operations", property(lambda _self: True))
    monkeypatch.setattr(slack_ask, "get_slack_user_info", AsyncMock(return_value=None))
    asker = User(identities=[UserIdentity(provider="github", external_id="1", login="octocat")])
    monkeypatch.setattr(User, "for_identity", AsyncMock(return_value=asker))
    monkeypatch.setattr(
        slack_service.LangSmithLogin, "authorize_user", AsyncMock(return_value="ls_token")
    )
    monkeypatch.setattr(slack_ask.common, "get_valid_access_token", AsyncMock(return_value="gho_x"))
    _patch_channel(monkeypatch, [])
    monkeypatch.setattr(slack_ask, "get_slack_user_names", AsyncMock(return_value={}))


@pytest.mark.asyncio
@pytest.mark.usefixtures("linked_asker")
@pytest.mark.parametrize(
    "reply_thread_ts,selected_message", [("", ""), ("123.456", ""), ("", "Ship the migration")]
)
async def test_command_thread_stays_private_and_can_reply_after_task_wakeup(
    monkeypatch: pytest.MonkeyPatch, reply_thread_ts: str, selected_message: str
) -> None:
    status = AsyncMock()
    monkeypatch.setattr("openswe.slack.thinking.set_slack_thread_status", status)
    monkeypatch.setattr(slack_ask, "add_slack_reaction", AsyncMock())
    monkeypatch.setattr(slack_ask, "acknowledge_slack_command", AsyncMock())
    upsert = AsyncMock(return_value=True)
    dispatch = AsyncMock()
    monkeypatch.setattr(
        slack_ask.common,
        "get_slack_repo_config",
        AsyncMock(return_value=slack_ask.common.SlackRepoResolution()),
    )
    monkeypatch.setattr(slack_ask.common, "upsert_agent_thread_metadata", upsert)
    monkeypatch.setattr(slack_ask, "dispatch_agent_run", dispatch)

    request = slack_ask.SlackAskRequest(
        channel_id="C1",
        user_id="U1",
        question="why?",
        selected_message=selected_message,
        thread_id="t-1",
        team_id="T1",
        reply_thread_ts=reply_thread_ts,
        message_ts="123.789" if reply_thread_ts else "",
        response_url="" if reply_thread_ts else "https://hooks.slack.com/commands/T1/1/x",
    )
    await slack_ask.process_slack_ask(request)

    assert upsert.await_args.kwargs["unlisted"] is True
    assert upsert.await_args.kwargs["visibility"] == "private"
    assert upsert.await_args.kwargs["owner_login"] == "octocat"
    configurable = dispatch.await_args.args[2]
    assert configurable["slack_ask"] is True
    assert configurable["slack_thread"]["triggering_user_id"] == "U1"
    assert "thread_ts" not in configurable["slack_thread"]
    status.assert_not_awaited()

    client = AsyncMock()
    saved = upsert.await_args.kwargs
    client.threads.get.return_value = {
        "metadata": {
            "owner_type": "user",
            "owner_login": saved["owner_login"],
            "visibility": saved["visibility"],
            "source": saved["source"],
            "source_context": saved["source_context"].dump(),
        }
    }
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "octocat")
    owner = User(identities=[UserIdentity(provider="github", external_id="1", login="octocat")])
    monkeypatch.setattr(task_schemas, "user_for_login", AsyncMock(return_value=owner))
    monkeypatch.setattr(task_service, "langgraph_client", lambda: client)
    monkeypatch.setattr(task_service, "get_profile", AsyncMock(return_value={}))
    monkeypatch.setattr(task_service, "resolve_run_email", AsyncMock(return_value=None))
    resumed = await task_service.recipient_config(request.thread_id)

    from openswe.run_config import RunConfig

    assert RunConfig.parse(resumed).slack_context
    monkeypatch.setattr(slack_reply, "get_config", lambda: {"configurable": resumed})
    monkeypatch.setattr(slack_reply, "get_langgraph_client", lambda: client)
    monkeypatch.setattr(slack_reply.event_claims, "claim", AsyncMock(return_value=True))
    monkeypatch.setattr(slack_reply, "claim_slack_event", AsyncMock(return_value=True))
    monkeypatch.setattr(slack_reply, "settle_slack_thread_status", AsyncMock())
    replace = AsyncMock(return_value=True)
    post = AsyncMock(return_value="123.999")
    remove_reaction = AsyncMock()
    monkeypatch.setattr(slack_reply, "replace_slack_command_message", replace)
    monkeypatch.setattr(slack_reply, "post_slack_thread_reply_with_ts", post)
    monkeypatch.setattr(slack_reply, "remove_slack_reaction", remove_reaction)

    assert await slack_reply.slack_reply("the worker's answer", "final") == {"success": True}
    if reply_thread_ts:
        assert post.await_args.args == ("C1", reply_thread_ts, "the worker's answer")
        # The conversation's own agent reads the answer from Slack after the question.
        assert SlackNotice.marks(post.await_args.kwargs)
        assert (
            post.await_args.kwargs["blocks"][-1]["block_id"]
            == SlackNotice(kind="btw", key="123.789").block_id
        )
        remove_reaction.assert_awaited_once_with("C1", "123.789", "hourglass_flowing_sand")
        replace.assert_not_awaited()
    else:
        assert replace.await_args.args == (request.response_url, "the worker's answer")
        post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.usefixtures("linked_asker")
async def test_channel_context_stays_inside_its_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_channel(
        monkeypatch,
        [SlackMessage(ts=f"{index}.000000", user="U2", text="x" * 4000) for index in range(1, 30)],
    )
    monkeypatch.setattr(slack_ask, "get_slack_user_names", AsyncMock(return_value={}))

    context = await slack_ask._channel_context("C1")

    assert len(context) <= slack_ask._CHANNEL_CONTEXT_MAX_CHARS
    assert context.startswith(slack_ask._CHANNEL_CONTEXT_TRIMMED)
    assert "29.000000" in context


def test_unlisted_threads_stay_out_of_the_thread_list() -> None:
    filters: dict[str, Any] = {"resolved": None, "source": None, "query": None}

    assert _metadata_matches_filters({"source": "slack"}, **filters)
    assert not _metadata_matches_filters({"source": "slack", "unlisted": True}, **filters)


@pytest.mark.asyncio
async def test_ask_mode_reply_is_ephemeral(monkeypatch: pytest.MonkeyPatch) -> None:
    post = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_reply, "post_slack_ephemeral_reply", post)
    monkeypatch.setattr(
        slack_reply,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "thread-1",
                "source": "slack",
                "slack_ask": True,
                "slack_thread": {"channel_id": "C1", "triggering_user_id": "U1"},
            }
        },
    )

    result = await slack_reply.slack_reply("the answer", "final")

    assert result == {"success": True}
    assert post.await_args.args == ("C1", "U1", "the answer")
    assert post.await_args.kwargs["blocks"] == [{"type": "markdown", "text": "the answer"}]
    assert post.await_args.kwargs["agent_thread_id"] == "thread-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "blocks", "posted"),
    [
        ("the answer", None, True),
        ("the answer", [], True),
        ("the answer", [{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}], True),
        ("x" * 200_000, None, False),
    ],
)
async def test_a_btw_answer_is_posted_as_a_notice_or_not_at_all(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    blocks: list[dict[str, Any]] | None,
    posted: bool,
) -> None:
    configurable = {
        "thread_id": "thread-1",
        "source": "slack",
        "slack_ask": True,
        "slack_by_the_way_thread_ts": "123.456",
        "slack_by_the_way_message_ts": "123.789",
        "slack_thread": {"channel_id": "C1", "triggering_user_id": "U1"},
    }
    monkeypatch.setattr(slack_reply, "get_config", lambda: {"configurable": configurable})
    monkeypatch.setattr(slack_reply.event_claims, "claim", AsyncMock(return_value=True))
    monkeypatch.setattr(slack_reply, "settle_slack_thread_status", AsyncMock())
    monkeypatch.setattr(slack_reply, "remove_slack_reaction", AsyncMock())
    post = AsyncMock(return_value="123.999")
    monkeypatch.setattr(slack_reply, "post_slack_thread_reply_with_ts", post)

    result = await slack_reply.slack_reply(message, "final", blocks=blocks)

    assert result["success"] is posted
    if posted:
        # Slack drops a markdown block's block_id, so the notice rides on a context block.
        notice = post.await_args.kwargs["blocks"][-1]
        assert notice["type"] == "context"
        assert notice["block_id"] == SlackNotice(kind="btw", key="123.789").block_id
    else:
        post.assert_not_awaited()


def _ask_config(response_url: str) -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": "thread-1",
            "source": "slack",
            "slack_ask": True,
            "slack_ask_response_url": response_url,
            "slack_thread": {"channel_id": "C1", "triggering_user_id": "U1"},
        }
    }


@pytest.mark.asyncio
async def test_an_unreplaceable_acknowledgement_falls_back_to_a_fresh_ephemeral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_reply, "replace_slack_command_message", AsyncMock(return_value=False))
    monkeypatch.setattr(slack_reply, "post_slack_ephemeral_reply", post)
    monkeypatch.setattr(slack_reply, "claim_slack_event", AsyncMock(return_value=True))
    monkeypatch.setattr(
        slack_reply, "get_config", lambda: _ask_config("https://hooks.slack.com/commands/T1/1/x")
    )

    assert await slack_reply.slack_reply("the answer", "final") == {"success": True}

    assert post.await_args.args == ("C1", "U1", "the answer")


@pytest.mark.asyncio
@pytest.mark.usefixtures("linked_asker")
async def test_a_by_the_way_question_for_mda_is_prepared_not_dispatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reaction = AsyncMock()
    monkeypatch.setattr(slack_ask, "add_slack_reaction", reaction)
    monkeypatch.setattr(
        slack_ask.common,
        "get_slack_repo_config",
        AsyncMock(return_value=slack_ask.common.SlackRepoResolution()),
    )
    upsert = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_ask.common, "upsert_agent_thread_metadata", upsert)
    dispatch = AsyncMock()
    monkeypatch.setattr(slack_ask, "dispatch_agent_run", dispatch)
    monkeypatch.setattr(slack_ask, "fetch_slack_thread_messages", AsyncMock(return_value=[]))
    remembered = SlackRequest(
        channel_id="C1",
        thread_ts="123.456",
        original_message_ts="123.789",
        user_id="U1",
        team_id="T1",
        thread_id="side-1",
        by_the_way="why?",
    )

    run = await slack_ask.prepare_ask_channel_run(slack_ask.by_the_way_request(remembered))

    assert run is not None
    dispatch.assert_not_awaited()
    # MDA's Slack channel shows the run as working; the backend adds no reaction of its own.
    reaction.assert_not_awaited()
    assert upsert.await_args.args[0] == "side-1"
    assert upsert.await_args.kwargs["unlisted"] is True
    assert run.configurable["slack_by_the_way_thread_ts"] == "123.456"
    assert run.configurable["slack_by_the_way_message_ts"] == "123.789"
    assert run.input["messages"]


@pytest.mark.asyncio
async def test_a_by_the_way_question_for_mda_without_a_question_starts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refuse = AsyncMock()
    monkeypatch.setattr(slack_ask, "_refuse", refuse)
    request = slack_ask.SlackAskRequest(
        channel_id="C1",
        user_id="U1",
        question="",
        thread_id="side-1",
        command=slack_ask.BY_THE_WAY_COMMAND,
        reply_thread_ts="123.456",
        message_ts="123.789",
    )

    assert await slack_ask.prepare_ask_channel_run(request) is None
    refuse.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("held_thread_id", "read"),
    [("", "question-thread"), ("conversation-thread", "conversation-thread")],
    ids=["its-own-thread", "a-branch"],
)
async def test_a_question_quotes_only_what_its_thread_never_got(
    monkeypatch: pytest.MonkeyPatch, held_thread_id: str, read: str
) -> None:
    raw = [
        {"ts": "1.0", "user": "U2", "text": "the build is red"},
        {"ts": "2.0", "user": "U3", "text": "only said in Slack"},
    ]
    monkeypatch.setattr(slack_ask, "fetch_slack_thread_messages", AsyncMock(return_value=raw))
    monkeypatch.setattr(slack_ask, "get_slack_user_names", AsyncMock(return_value={}))
    held = AsyncMock(return_value={"1.0"})
    monkeypatch.setattr(slack_ask.forks, "held_timestamps", held)
    request = slack_ask.SlackAskRequest(
        channel_id="C1",
        user_id="U1",
        question="why?",
        thread_id="question-thread",
        reply_thread_ts="1.0",
        message_ts="3.0",
        held_thread_id=held_thread_id,
    )

    context = await slack_ask._request_context(request)

    # A branch's new thread starts with what its conversation's thread holds.
    held.assert_awaited_once_with(read)
    assert "only said in Slack" in context
    assert "the build is red" not in context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("linked", "langsmith_status", "github_reason", "needed"),
    [
        (False, None, None, "unlinked"),
        (True, 401, None, "revoked"),
        (True, 403, None, "revoked"),
        (True, None, None, None),
        (True, None, "revoked", None),
        (True, None, "unlinked", None),
    ],
)
async def test_the_account_gate_runs_without_a_thread(
    monkeypatch: pytest.MonkeyPatch,
    linked: bool,
    langsmith_status: int | None,
    github_reason: str | None,
    needed: str | None,
) -> None:
    user = User(identities=[UserIdentity(provider="github", external_id="1", login="ada")])
    monkeypatch.setattr(User, "for_identity", AsyncMock(return_value=user if linked else None))
    monkeypatch.setattr(
        slack_service.LangSmithLogin,
        "authorize_user",
        AsyncMock(side_effect=HTTPException(langsmith_status) if langsmith_status else None),
    )
    monkeypatch.setattr(
        slack_ask.common, "account_link_reason", AsyncMock(return_value=github_reason)
    )
    email_lookup = AsyncMock(side_effect=AssertionError("Slack must be explicitly linked"))
    monkeypatch.setattr(User, "login_for_email", email_lookup)

    gate = await slack_service.account_link_needed("U1")

    assert (gate.reason if gate else None) == needed
    email_lookup.assert_not_awaited()
    if not needed:
        authorization = await slack_service.SlackAuthorization.resolve("U1")
        config: dict[str, JsonValue] = {"repo": {"owner": "org", "name": "repo"}}
        authorization.configure(config)
        assert authorization.identity is not None
        if github_reason:
            assert authorization.identity.subject == "ada"
            assert authorization.github_login is None
            assert config["user_id"] == str(user.id)
            assert config["github_linked"] is False
            assert config["repo"] is None
            assert config["repo_explicitly_none"] is True
            assert "github_login" not in config
        else:
            assert authorization.identity.subject == "ada"
            assert config["github_login"] == "ada"


@pytest.mark.asyncio
@pytest.mark.usefixtures("linked_asker")
async def test_a_question_without_github_starts_a_canonical_nonrepository_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_ask.common, "account_link_reason", AsyncMock(return_value="revoked"))
    repo_lookup = AsyncMock(
        side_effect=AssertionError("A GitHub-free run must not default a repository")
    )
    monkeypatch.setattr(slack_ask.common, "get_slack_repo_config", repo_lookup)
    upsert = AsyncMock(return_value=True)
    dispatch = AsyncMock()
    monkeypatch.setattr(slack_ask.common, "upsert_agent_thread_metadata", upsert)
    monkeypatch.setattr(slack_ask, "dispatch_agent_run", dispatch)

    await slack_ask.process_slack_ask(
        slack_ask.SlackAskRequest(channel_id="C1", user_id="U1", question="hello", thread_id="t-1")
    )

    config = dispatch.await_args.args[2]
    assert config["github_linked"] is False
    assert config["repo"] is None
    assert config["repo_explicitly_none"] is True
    assert "github_login" not in config
    saved = upsert.await_args.kwargs
    assert saved["visibility"] == "private"
    assert saved["owner_login"] == "octocat"
    assert saved["identity"].subject == saved["owner_login"]
    repo_lookup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.usefixtures("linked_asker")
async def test_a_github_linked_question_requires_langsmith_sign_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        slack_service.LangSmithLogin, "authorize_user", AsyncMock(side_effect=HTTPException(401))
    )
    dispatch = AsyncMock()
    prompt = AsyncMock()
    monkeypatch.setattr(slack_ask, "dispatch_agent_run", dispatch)
    monkeypatch.setattr(slack_ask.common, "post_account_link_prompt", prompt)

    await slack_ask.process_slack_ask(
        slack_ask.SlackAskRequest(channel_id="C1", user_id="U1", question="hello", thread_id="t-1")
    )

    dispatch.assert_not_awaited()
    assert prompt.await_args.kwargs["langsmith"] is True
    assert prompt.await_args.kwargs["reason"] == "revoked"


@pytest.mark.asyncio
@pytest.mark.parametrize("github_reason", [None, "revoked"])
async def test_slack_authorization_retains_only_the_same_persons_immutable_owner(
    monkeypatch: pytest.MonkeyPatch,
    github_reason: str | None,
) -> None:
    user = User(identities=[UserIdentity(provider="github", external_id="1", login="ada")])
    monkeypatch.setattr(User, "for_identity", AsyncMock(return_value=user))
    monkeypatch.setattr(
        slack_service.LangSmithLogin, "authorize_user", AsyncMock(return_value="ls_token")
    )
    monkeypatch.setattr(
        slack_ask.common, "account_link_reason", AsyncMock(return_value=github_reason)
    )
    authorization = await slack_service.SlackAuthorization.resolve("U1")

    assert authorization.subject_for({"owner_login": "ada"}) == "ada"
    assert authorization.subject_for({"owner_login": f"user:{user.id}"}) == f"user:{user.id}"
    assert authorization.subject_for({"owner_login": "someone-else"}) == "ada"


@pytest.mark.asyncio
async def test_slack_authorization_does_not_allow_a_langsmith_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(User, "for_identity", AsyncMock(return_value=User()))
    monkeypatch.setattr(
        slack_service.LangSmithLogin, "authorize_user", AsyncMock(side_effect=HTTPException(503))
    )

    with pytest.raises(HTTPException) as error:
        await slack_service.SlackAuthorization.resolve("U1")
    assert error.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("github_reason", ["unlinked", "revoked"])
async def test_a_slack_mention_without_github_prepares_a_canonical_chat_run(
    monkeypatch: pytest.MonkeyPatch,
    github_reason: str,
) -> None:
    from tests.slack.test_slack_context import _setup_slack_mention_fakes

    captured: dict[str, object] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)
    monkeypatch.setattr(slack_service, "persist_display_name", AsyncMock())
    monkeypatch.setattr(
        slack_service.THREADS, "state", AsyncMock(return_value={"values": {"messages": []}})
    )
    monkeypatch.setattr(
        slack_ask.common, "account_link_reason", AsyncMock(return_value=github_reason)
    )
    upsert = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_ask.common, "upsert_agent_thread_metadata", upsert)
    default_repo = AsyncMock(
        side_effect=AssertionError("A GitHub-free run must not default a repository")
    )
    monkeypatch.setattr(slack_service, "workspace_scoped_default_repo", default_repo)

    prepared, _ = await slack_service.prepare_slack_channel_run(
        SlackRequest(
            channel_id="C1",
            thread_ts="1.0",
            event_ts="1.1",
            user_id="U123",
            text="hello",
            bot_user_id="UBOT",
            thread_id="t-1",
        ),
        slack_ask.common.SlackRepoResolution(),
    )

    assert isinstance(prepared, slack_service.SlackChannelRun)
    config = prepared.configurable
    assert config["github_linked"] is False
    assert config["repo"] is None
    assert config["repo_explicitly_none"] is True
    assert "github_login" not in config
    assert upsert.await_args.kwargs["owner_login"] == "mason-gh"
    default_repo.assert_not_awaited()


@pytest.mark.asyncio
async def test_slack_repository_defaults_use_only_the_explicit_account_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SimpleNamespace(threads=SimpleNamespace(get=AsyncMock(return_value={"metadata": {}})))
    monkeypatch.setattr(slack_ask.common, "get_client", lambda **_kwargs: client)
    linked_login = AsyncMock(return_value="explicit-user")
    email_login = AsyncMock(side_effect=AssertionError("Email must not associate Slack accounts"))
    monkeypatch.setattr(User, "login_for_slack", linked_login)
    monkeypatch.setattr(User, "login_for_email", email_login)
    profile_repo = AsyncMock(return_value={"owner": "acme", "name": "billing"})
    monkeypatch.setattr(slack_ask.common, "get_profile_default_repo", profile_repo)

    result = await slack_ask.common.get_slack_repo_config(
        "C1",
        "1.0",
        slack_user_id="U1",
        channel_context=SlackChannelContext(name="eng"),
        thread_id="t-1",
    )

    assert result.repo is not None
    assert result.repo.full_name == "acme/billing"
    linked_login.assert_awaited_once_with("U1")
    profile_repo.assert_awaited_once_with("explicit-user")
    email_login.assert_not_awaited()
