"""What Open SWE's GitHub channel starts for each GitHub delivery."""

import uuid
from typing import Any, cast, get_args
from unittest.mock import AsyncMock

import pytest
import rt.github as github
from managed_deepagents import (
    ChannelRun,
    GitHubAccount,
    GitHubChannelThread,
    GitHubEvent,
    GitHubEventResult,
    GitHubInstallationEvent,
    GitHubInstallationEventKey,
    GitHubInstallationPayload,
    GitHubPayload,
    SupportedEventKey,
)
from managed_deepagents._channels.github_types import GitHubRepository, GitHubSender
from managed_deepagents._channels.http.types import ChannelName
from managed_deepagents._channels.run_input import RunChannel, normalize_run_inputs
from pydantic import BaseModel
from rt.github_issues import IssueMention
from rt.github_routes import Route, route
from runtime_api.endpoint import Endpoint
from runtime_api.github import (
    ACT_ON_GITHUB_DELIVERY,
    GITHUB_AUTOMATION_RULES,
    GITHUB_ISSUE_FACTS,
    RECORD_GITHUB_DELIVERY,
    ActOnGitHubDelivery,
    GitHubAutomationRules,
    GitHubAutomationRulesRequest,
    GitHubDeliveryActed,
    GitHubDeliveryFacts,
    GitHubIssueFacts,
    GitHubIssueFactsRequest,
    GitHubIssueRun,
    GitHubThreadRun,
    PlannedGitHubIssueRun,
)

_THREAD = GitHubChannelThread(id=str(uuid.uuid4()), channel=ChannelName(name="github"))
_TAGS = ["@openswe", "@open-swe", "@openswe-dev"]
_FACTS = GitHubDeliveryFacts(routable=True, allowed=True, auto_review=True, tags=_TAGS)
_REPOSITORY = {"name": "api", "owner": {"login": "acme"}}


def _facts(**changes: object) -> GitHubDeliveryFacts:
    return _FACTS.model_copy(update=changes)


_SENDER = {"login": "ada", "id": 2}


def _issue(action: str = "opened", body: str = "@openswe fix it", **extra: object) -> dict:
    issue = {"id": 70, "number": 7, "title": "Crash", "body": body, "user": {"login": "bob"}}
    return {
        "action": action,
        "issue": issue,
        "repository": _REPOSITORY,
        "sender": _SENDER,
        **extra,
    }


def _comment(body: str, *, on_pr: bool, action: str = "created") -> dict:
    issue: dict[str, object] = {"id": 70, "number": 7, "title": "Crash", "body": ""}
    if on_pr:
        issue["pull_request"] = {"url": "https://api.github.com/repos/acme/api/pulls/7"}
    return {
        "action": action,
        "issue": issue,
        "comment": {"id": 5, "body": body, "user": _SENDER, "created_at": "2026-01-01"},
        "repository": _REPOSITORY,
        "sender": _SENDER,
    }


def _review(action: str = "submitted", body: str = "") -> dict:
    return {
        "action": action,
        "review": {"body": body, "state": "commented"},
        "pull_request": {"number": 7},
        "repository": _REPOSITORY,
    }


@pytest.mark.parametrize(
    ("event", "payload", "facts", "from_app", "routed"),
    [
        pytest.param(
            "issues", _issue(), _facts(routable=False), False, Route(), id="unowned-repository"
        ),
        pytest.param(
            "pull_request",
            {"action": "opened"},
            _facts(allowed=False),
            False,
            Route(),
            id="outside-the-allowlist",
        ),
        pytest.param(
            "merge_group",
            {"action": "checks_requested"},
            _FACTS,
            False,
            Route(),
            id="merge-group",
        ),
        pytest.param(
            "pull_request",
            {"action": "opened"},
            _FACTS,
            True,
            Route(automations=True),
            id="pr-opened-by-the-agent",
        ),
        pytest.param(
            "pull_request",
            {"action": "opened"},
            _facts(gated=True),
            False,
            Route(),
            id="pr-opened-by-a-sender-the-public-repo-gate-turns-away",
        ),
        pytest.param(
            "pull_request_review_thread",
            {"action": "resolved"},
            _FACTS,
            False,
            Route(),
            id="review-thread-resolved",
        ),
        pytest.param(
            "pull_request",
            {"action": "ready_for_review"},
            _facts(auto_review=False),
            False,
            Route(),
            id="pr-ready-without-review",
        ),
        pytest.param(
            "pull_request",
            {"action": "synchronize"},
            _facts(auto_review=False),
            True,
            Route(),
            id="pr-pushed",
        ),
        pytest.param(
            "pull_request",
            {"action": "closed"},
            _FACTS,
            False,
            Route(automations=True),
            id="pr-closed",
        ),
        pytest.param(
            "pull_request",
            {"action": "edited"},
            _FACTS,
            False,
            Route(),
            id="pr-edited",
        ),
        pytest.param("push", {}, _FACTS, True, Route(), id="push"),
        pytest.param(
            "push",
            {},
            _facts(auto_review=False),
            False,
            Route(),
            id="push-without-review",
        ),
        pytest.param(
            "check_run",
            {"action": "completed"},
            _FACTS,
            False,
            Route(),
            id="ci",
        ),
        pytest.param(
            "status", {}, _facts(allowed=False), False, Route(), id="ci-outside-the-allowlist"
        ),
        pytest.param(
            "pull_request_review",
            _review(),
            _FACTS,
            False,
            Route(comment=True),
            id="review-submitted",
        ),
        pytest.param(
            "pull_request_review",
            _review(body="@openswe address these"),
            _FACTS,
            True,
            Route(),
            id="the-agents-own-review",
        ),
        pytest.param(
            "pull_request_review",
            _review("dismissed"),
            _FACTS,
            False,
            Route(),
            id="review-dismissed",
        ),
        pytest.param(
            "issues",
            _issue(),
            _FACTS,
            False,
            Route(automations=True, issue_run=True),
            id="issue-mention",
        ),
        pytest.param(
            "issues",
            _issue(body="no handle"),
            _FACTS,
            False,
            Route(automations=True),
            id="issue-without-mention",
        ),
        pytest.param(
            "issues",
            _issue(),
            _FACTS,
            True,
            Route(automations=True),
            id="issue-the-agent-opened",
        ),
        pytest.param(
            "issues",
            _issue("edited", changes={"labels": {}}),
            _FACTS,
            False,
            Route(),
            id="issue-edit-without-text",
        ),
        pytest.param(
            "issues",
            _issue("reopened"),
            _facts(allowed=False),
            False,
            Route(),
            id="issue-outside-the-allowlist",
        ),
        pytest.param(
            "issue_comment",
            _comment("@openswe try again", on_pr=False),
            _FACTS,
            False,
            Route(issue_run=True),
            id="issue-comment-mention",
        ),
        pytest.param(
            "issue_comment",
            _comment("@openswe-preview try again", on_pr=False),
            _FACTS,
            False,
            Route(),
            id="another-deployments-handle",
        ),
        pytest.param(
            "issue_comment",
            _comment("@openswe try again", on_pr=False),
            _FACTS,
            True,
            Route(),
            id="the-agents-own-comment",
        ),
        pytest.param(
            "issue_comment",
            _comment("@openswe fix CI", on_pr=True),
            _FACTS,
            False,
            Route(comment=True, mentioned=True),
            id="pr-comment-mention",
        ),
        pytest.param(
            "issue_comment",
            _comment("CI is failing", on_pr=True, action="edited"),
            _FACTS,
            False,
            Route(comment=True),
            id="pr-comment-untagged",
        ),
        pytest.param(
            "issue_comment",
            _comment("!merge", on_pr=True),
            _FACTS,
            False,
            Route(),
            id="untagged-workflow-command",
        ),
        pytest.param(
            "installation_repositories",
            {"action": "added", "repositories_added": [], "repositories_removed": []},
            _facts(routable=True),
            False,
            Route(),
            id="installation-changed",
        ),
        pytest.param(
            "issues",
            _issue("closed"),
            _FACTS,
            False,
            Route(),
            id="issue-closed-only-logged",
        ),
        pytest.param(
            "pull_request",
            {"action": "labeled"},
            _FACTS,
            False,
            Route(),
            id="pr-labeled-wakes-the-queue",
        ),
        pytest.param(
            "pull_request_review_comment",
            {
                "action": "created",
                "comment": {"body": "The finding is wrong", "in_reply_to_id": 1},
                "repository": _REPOSITORY,
            },
            _FACTS,
            False,
            Route(comment=True),
            id="review-comment-reply",
        ),
    ],
)
def test_a_delivery_starts_what_the_backends_webhook_did(
    event: str, payload: dict, facts: GitHubDeliveryFacts, from_app: bool, routed: Route
) -> None:
    assert route(event, payload, facts, from_app=from_app) == routed


class _Backend:
    """The backend's channel API, as the handler asks it."""

    def __init__(self) -> None:
        self.facts = _FACTS
        self.issue_facts = GitHubIssueFacts(
            conversation_started=False, sender_email_known=True, trusted=["ada"]
        )
        self.rules = GitHubAutomationRules(automations=[])
        self.rules_error: Exception | None = None
        self.planned = PlannedGitHubIssueRun(
            starts=True,
            run=GitHubIssueRun(
                context={"__run_token": "signed"},
                metadata={"channel": "github"},
                webhook="https://backend.example/webhooks/run-complete",
            ),
        )
        self.automations: list[GitHubThreadRun] = []
        self.comment_run: GitHubThreadRun | None = None
        self.calls: list[tuple[str, BaseModel, str]] = []

    async def __call__(
        self, endpoint: Endpoint[BaseModel, BaseModel], request: BaseModel, *, msg_id: str
    ) -> BaseModel:
        self.calls.append((endpoint.name, request, msg_id))
        if endpoint is RECORD_GITHUB_DELIVERY:
            return self.facts
        if endpoint is GITHUB_ISSUE_FACTS:
            asked = cast(GitHubIssueFactsRequest, request).logins
            trusted = [login for login in self.issue_facts.trusted if login in asked]
            return self.issue_facts.model_copy(update={"trusted": trusted})
        if endpoint is GITHUB_AUTOMATION_RULES:
            if self.rules_error is not None:
                raise self.rules_error
            return self.rules
        assert endpoint is ACT_ON_GITHUB_DELIVERY
        act = cast(ActOnGitHubDelivery, request)
        return GitHubDeliveryActed(
            issue_run=self.planned if act.issue_run is not None else None,
            automations=self.automations if act.automations else [],
            comment_run=self.comment_run if act.comment else None,
        )

    def called[Request: BaseModel](self, endpoint: Endpoint[Request, Any]) -> list[Request]:
        return [cast(Request, request) for name, request, _ in self.calls if name == endpoint.name]


class _GitHub:
    """GitHub's API, as the App reads an issue's comments and reacts to one."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.comments: list[dict[str, object]] = []
        self.listed = AsyncMock(side_effect=lambda: list(self.comments))
        self.acknowledged = AsyncMock()
        monkeypatch.setattr(IssueMention, "comments", lambda _mention: self.listed())
        monkeypatch.setattr(IssueMention, "acknowledge", lambda _mention: self.acknowledged())


@pytest.fixture
def gh(monkeypatch: pytest.MonkeyPatch) -> _GitHub:
    return _GitHub(monkeypatch)


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch, gh: _GitHub) -> _Backend:
    backend = _Backend()
    monkeypatch.setattr(github, "ask_backend", backend)
    return backend


def _event(name: str, payload: dict, *, from_app: bool = False) -> GitHubEvent:
    action = payload.get("action")
    return GitHubEvent(
        key=cast(SupportedEventKey, name if action is None else f"{name}.{action}"),
        name=name,
        action=action,
        delivery_id="delivery-1",
        installation_id=1,
        repository=GitHubRepository(
            id=1, owner="acme", name="api", full_name="acme/api", private=True
        ),
        sender=GitHubSender(id=2, login="ada", type="User"),
        payload=cast(GitHubPayload, payload),
        from_app=from_app,
    )


def _msg_ids(backend: _Backend) -> set[str]:
    return {msg_id for *_, msg_id in backend.calls}


async def test_an_issue_mention_starts_the_run_it_wrote_on_the_issues_thread(
    backend: _Backend, gh: _GitHub
) -> None:
    started = _runs(await github.on_event(_event("issues", _issue()), _THREAD))

    [act] = backend.called(ACT_ON_GITHUB_DELIVERY)
    assert act.issue_run is not None
    assert (str(act.issue_run.thread_id), act.issue_run.waiting) == (_THREAD.id, False)
    # The run's input is the one written here; the backend only signed it.
    assert started == [
        {
            "input": act.issue_run.input,
            "context": {"__run_token": "signed"},
            "metadata": {"channel": "github"},
            "webhook": "https://backend.example/webhooks/run-complete",
        }
    ]
    messages = cast(list[dict[str, Any]], cast(dict[str, Any], act.issue_run.input)["messages"])
    assert "## GitHub Issue: #7 - Issue ID: 70" in messages[1]["content"]
    # A new conversation: in the repository's own workspace, with the issue's comments.
    assert act.issue_run.workspace is None
    gh.listed.assert_awaited_once()
    # A list of one run, as a dict alone would be state: MDA starts it on the conversation.
    [run] = normalize_run_inputs(started, RunChannel(name="github", provider="github"))
    assert "thread" not in run
    # Log it, learn who's who and the repository's automations, then act.
    assert _msg_ids(backend) == {
        "msg_delivery-1_record",
        "msg_delivery-1_issue",
        "msg_delivery-1_automations",
        "msg_delivery-1_act",
    }
    [facts] = backend.called(GITHUB_ISSUE_FACTS)
    assert (facts.sender, facts.logins) == ("ada", ["ada", "bob"])
    assert act.automations == []


async def test_the_people_only_the_comments_name_are_looked_up_once_more(
    backend: _Backend, gh: _GitHub
) -> None:
    backend.issue_facts = backend.issue_facts.model_copy(update={"trusted": ["ada", "carol"]})
    gh.comments = [
        {"body": "seen it", "author": "carol", "created_at": "2025-12-31", "comment_id": 1},
        {"body": "me too", "author": "dave", "created_at": "2025-12-31", "comment_id": 2},
    ]

    await github.on_event(_event("issue_comment", _comment("@openswe go", on_pr=False)), _THREAD)

    first, more = backend.called(GITHUB_ISSUE_FACTS)
    assert (first.logins, more.logins) == (["ada"], ["carol", "dave"])
    [act] = backend.called(ACT_ON_GITHUB_DELIVERY)
    assert act.issue_run is not None
    contents = [
        message["content"]
        for message in cast(list[dict[str, Any]], cast(dict, act.issue_run.input)["messages"])
    ]
    # Carol is a user, so her words aren't fenced; Dave's are.
    assert any("seen it" in text and "dangerous" not in text for text in contents)
    assert any("me too" in text and "dangerous" in text for text in contents)
    # The mention's own comment joins the comments GitHub listed.
    assert any('comment_id="5"' in text for text in contents)
    gh.acknowledged.assert_awaited_once()


async def test_a_follow_up_mention_is_the_persons_own_message(
    backend: _Backend, gh: _GitHub
) -> None:
    backend.issue_facts = backend.issue_facts.model_copy(
        update={"conversation_started": True, "thread_workspace": "team-a"}
    )

    await github.on_event(
        _event("issue_comment", _comment("@openswe also this", on_pr=False)), _THREAD
    )

    [act] = backend.called(ACT_ON_GITHUB_DELIVERY)
    assert act.issue_run is not None
    # It stays in the workspace its thread started in, and reads no comments.
    assert act.issue_run.workspace == "team-a"
    gh.listed.assert_not_awaited()
    [message] = cast(list[dict[str, Any]], cast(dict, act.issue_run.input)["messages"])
    assert message["content"].startswith('<input-message sender="github:2"')
    assert "**ada:**\n@openswe also this" in message["content"]


@pytest.mark.parametrize(
    ("facts", "event", "payload"),
    [
        pytest.param(
            {"trusted": []},
            "issue_comment",
            _comment("@openswe go", on_pr=False),
            id="commenter-not-a-user",
        ),
        pytest.param({"sender_email_known": False}, "issues", _issue(), id="sender-email-unknown"),
    ],
)
async def test_a_mention_the_channel_refuses_asks_nothing_more(
    backend: _Backend, gh: _GitHub, facts: dict[str, object], event: str, payload: dict
) -> None:
    backend.issue_facts = backend.issue_facts.model_copy(update=facts)

    assert await github.on_event(_event(event, payload), _THREAD) is None

    assert ACT_ON_GITHUB_DELIVERY.name not in {name for name, *_ in backend.calls}
    gh.listed.assert_not_awaited()
    gh.acknowledged.assert_not_awaited()


async def test_a_mention_while_the_thread_waits_only_points_to_the_decision(
    backend: _Backend, gh: _GitHub
) -> None:
    backend.planned = PlannedGitHubIssueRun(starts=True)
    waiting = GitHubChannelThread(id=_THREAD.id, channel=_THREAD.channel, interrupted=True)

    started = await github.on_event(
        _event("issue_comment", _comment("@openswe go", on_pr=False)), waiting
    )

    assert started == [{"input": github.ACTS_WHILE_WAITING}]
    [act] = backend.called(ACT_ON_GITHUB_DELIVERY)
    assert act.issue_run is not None and act.issue_run.waiting is True
    assert act.issue_run.input is None
    # Nothing is written or acknowledged while the thread waits.
    assert backend.called(GITHUB_ISSUE_FACTS) == []
    gh.acknowledged.assert_not_awaited()


def _runs(started: GitHubEventResult) -> list[ChannelRun]:
    """The runs a handler started, as a list."""
    assert isinstance(started, list)
    return started


def _automation(prompt: str) -> GitHubThreadRun:
    return GitHubThreadRun(
        thread_id=uuid.uuid4(),
        input={"messages": [{"role": "user", "content": prompt}]},
        context={"__run_token": f"signed:{prompt}"},
        metadata={"channel": "github"},
    )


def _rules(*events: list[str]) -> GitHubAutomationRules:
    return GitHubAutomationRules.model_validate(
        {
            "automations": [
                {"id": f"a-{index}", "prompt": f"Do {index}", "events": fires_on}
                for index, fires_on in enumerate(events)
            ]
        }
    )


async def test_each_automation_starts_on_the_thread_the_backend_planned(
    backend: _Backend,
) -> None:
    triage, label = _automation("Triage"), _automation("Label")
    backend.rules = _rules(["issues.opened"], ["issues.opened"], ["pull_request.opened"])
    backend.automations = [triage, label]

    started = _runs(await github.on_event(_event("issues", _issue()), _THREAD))

    # The mention's run on the issue's conversation, and each automation's on its own thread.
    assert len(started) == 3
    assert [run.get("thread") for run in started[1:]] == [
        {"mode": "persistent", "id": str(triage.thread_id)},
        {"mode": "persistent", "id": str(label.thread_id)},
    ]
    assert started[1] == {
        "input": triage.input,
        "context": triage.context,
        "metadata": triage.metadata,
        "thread": {"mode": "persistent", "id": str(triage.thread_id)},
    }
    [rules] = backend.called(GITHUB_AUTOMATION_RULES)
    assert rules == GitHubAutomationRulesRequest(repository="acme/api", author="bob")
    # The two that fire on an opened issue, each with the prompt written here.
    [act] = backend.called(ACT_ON_GITHUB_DELIVERY)
    assert [(fired.automation_id, fired.event) for fired in act.automations] == [
        ("a-0", "issues.opened"),
        ("a-1", "issues.opened"),
    ]
    assert act.automations[0].prompt.startswith("Do 0\n\nThis automation was triggered")
    # MDA takes the list: one run on the conversation, the others on their threads.
    runs = normalize_run_inputs(started, RunChannel(name="github", provider="github"))
    assert ["thread" in run for run in runs] == [False, True, True]


async def test_automations_start_while_the_issues_thread_waits(backend: _Backend) -> None:
    backend.planned = PlannedGitHubIssueRun(starts=True)
    backend.rules = _rules(["issues.opened"])
    backend.automations = [_automation("Triage")]
    waiting = GitHubChannelThread(id=_THREAD.id, channel=_THREAD.channel, interrupted=True)

    started = _runs(await github.on_event(_event("issues", _issue()), waiting))

    assert started[0] == {"input": github.ACTS_WHILE_WAITING}
    assert len(normalize_run_inputs(started, RunChannel(name="github", provider="github"))) == 2
    assert started[1].get("thread") == {
        "mode": "persistent",
        "id": str(backend.automations[0].thread_id),
    }


async def test_a_pull_request_opened_starts_only_its_automations(backend: _Backend) -> None:
    backend.rules = _rules(["pull_request.opened"])
    backend.automations = [_automation("Summarize")]

    started = _runs(await github.on_event(_event("pull_request", {"action": "opened"}), _THREAD))

    assert len(started) == 1
    # The backend runs its own work as it records the delivery; this call only signs runs.
    [act] = backend.called(ACT_ON_GITHUB_DELIVERY)
    assert not act.work
    assert ([fired.automation_id for fired in act.automations], act.issue_run) == (["a-0"], None)


async def test_an_automation_that_fails_fires_nothing_and_fails_nothing(
    backend: _Backend,
) -> None:
    """Automations used to run in the backend's background, where one that failed
    stopped nothing else. The backend's work, such as a review, starts as it records
    the delivery, before any automation is worked out."""
    backend.rules_error = RuntimeError("the rules could not be read")

    started = await github.on_event(_event("pull_request", {"action": "opened"}), _THREAD)

    assert started is None
    assert [msg_id for *_, msg_id in backend.calls] == [
        "msg_delivery-1_record",
        "msg_delivery-1_automations",
    ]


async def test_an_automation_that_fails_keeps_the_mentions_run(
    backend: _Backend, gh: _GitHub
) -> None:
    backend.rules_error = RuntimeError("the rules could not be read")

    started = _runs(await github.on_event(_event("issues", _issue()), _THREAD))

    # The mention's run on the issue's conversation still starts.
    assert len(started) == 1
    [act] = [act for act in backend.called(ACT_ON_GITHUB_DELIVERY) if act.issue_run]
    assert act.automations == []


async def test_a_delivery_no_automation_matches_asks_nothing_more(backend: _Backend) -> None:
    backend.rules = _rules(["pull_request.merged"])

    closed = {"action": "closed", "pull_request": {"merged": False}}
    started = await github.on_event(_event("pull_request", closed), _THREAD)

    # The backend's work for the close starts as it records it; no automation fires.
    assert started is None
    assert backend.called(ACT_ON_GITHUB_DELIVERY) == []


async def test_a_mention_the_backend_refuses_starts_nothing(backend: _Backend, gh: _GitHub) -> None:
    backend.planned = PlannedGitHubIssueRun(starts=False)

    assert await github.on_event(_event("issues", _issue()), _THREAD) is None
    gh.acknowledged.assert_not_awaited()


async def test_a_comment_the_backend_runs_itself_starts_no_run_here(
    backend: _Backend,
) -> None:
    # The run goes to the agent's own thread, which the backend starts itself.
    started = await github.on_event(
        _event("issue_comment", _comment("@openswe fix CI", on_pr=True)), _THREAD
    )

    assert started is None
    [act] = backend.called(ACT_ON_GITHUB_DELIVERY)
    assert (act.work, act.comment, act.mentioned, act.issue_run) == ([], True, True, None)


async def test_a_comment_starts_on_the_pull_requests_own_thread(backend: _Backend) -> None:
    backend.comment_run = _automation("Fix CI")

    started = _runs(
        await github.on_event(
            _event("issue_comment", _comment("@openswe fix CI", on_pr=True)), _THREAD
        )
    )

    thread = {"mode": "persistent", "id": str(backend.comment_run.thread_id)}
    # A newer comment replaces the work on an older one, as the backend's dispatch did.
    assert started == [
        {
            "input": backend.comment_run.input,
            "context": backend.comment_run.context,
            "metadata": backend.comment_run.metadata,
            "thread": thread,
            "multitask_strategy": "interrupt",
        }
    ]
    [run] = normalize_run_inputs(started, RunChannel(name="github", provider="github"))
    assert (run.get("thread"), run.get("multitask_strategy")) == (thread, "interrupt")


async def test_an_installation_event_drops_the_cached_repository_listings(
    backend: _Backend,
) -> None:
    event = GitHubInstallationEvent(
        key="installation.created",
        name="installation",
        action="created",
        delivery_id="delivery-1",
        installation_id=1,
        account=GitHubAccount(id=3, login="acme"),
        sender=GitHubSender(id=2, login="ada", type="User"),
        payload=cast(
            GitHubInstallationPayload,
            {
                "action": "created",
                "installation": {"id": 1, "account": {"id": 3, "login": "acme"}},
                "sender": {"id": 2, "login": "ada", "type": "User"},
            },
        ),
    )

    assert await github.on_installation_event(event, _THREAD) is None
    # Handed to the backend, which drops everyone's cached listing; no run starts.
    [delivery] = backend.called(RECORD_GITHUB_DELIVERY)
    assert delivery.event == "installation"
    assert backend.called(ACT_ON_GITHUB_DELIVERY) == []


def test_the_channel_takes_every_event_the_app_sends() -> None:
    from channels.github import channel

    events = set(cast(list[str], channel.provider_config["events"]))
    assert {
        "issues.closed",
        "check_run.created",
        "installation.created",
        "pull_request_review_thread.resolved",
    } <= events
    assert events == set(get_args(SupportedEventKey)) | set(get_args(GitHubInstallationEventKey))
    # A review thread resolved or unresolved, which the backend's reviewer follows,
    # is the only subscription the backend's own webhook had beyond these.
    assert channel.provider_config["webhookEvents"] == [
        "check_run",
        "check_suite",
        "issue_comment",
        "issues",
        "merge_group",
        "pull_request",
        "pull_request_review",
        "pull_request_review_comment",
        "pull_request_review_thread",
        "push",
        "status",
        "workflow_run",
    ]


@pytest.mark.parametrize("action", ["resolved", "unresolved"])
async def test_a_review_thread_reaches_the_backend(backend: _Backend, action: str) -> None:
    """The backend's reviewer follows a review thread's resolution, as it picks its own
    work when the delivery is recorded; it starts no agent run."""
    payload = {"action": action, "pull_request": {"number": 7}, "repository": _REPOSITORY}

    assert await github.on_event(_event("pull_request_review_thread", payload), _THREAD) is None
    [delivery] = backend.called(RECORD_GITHUB_DELIVERY)
    assert (delivery.event, delivery.payload["action"]) == ("pull_request_review_thread", action)
    assert [name for name, *_ in backend.calls] == [RECORD_GITHUB_DELIVERY.name]


async def test_a_delivery_that_starts_nothing_is_only_logged(backend: _Backend) -> None:
    backend.facts = _facts(routable=False)

    assert await github.on_event(_event("issues", _issue()), _THREAD) is None
    assert [name for name, *_ in backend.calls] == [RECORD_GITHUB_DELIVERY.name]
