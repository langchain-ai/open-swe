"""What Open SWE's GitHub channel starts for each GitHub delivery."""

import uuid
from typing import Any, cast

import pytest
import rt.github as github
from managed_deepagents import (
    ChannelRunInput,
    ChannelRunOptions,
    GitHubChannelThread,
    GitHubEvent,
    GitHubEventResult,
    GitHubPayload,
    SupportedEventKey,
)
from managed_deepagents._channels.github_types import GitHubRepository, GitHubSender
from managed_deepagents._channels.http.types import ChannelName
from managed_deepagents._channels.run_input import RunChannel, normalize_run_inputs
from pydantic import BaseModel
from rt.github_routes import Route, route
from runtime_api.endpoint import Endpoint
from runtime_api.github import (
    ACT_ON_GITHUB_DELIVERY,
    RECORD_GITHUB_DELIVERY,
    ActOnGitHubDelivery,
    GitHubAutomationRun,
    GitHubDeliveryActed,
    GitHubDeliveryFacts,
    GitHubIssueRun,
    PlannedGitHubIssueRun,
)

_THREAD = GitHubChannelThread(id=str(uuid.uuid4()), channel=ChannelName(name="github"))
_TAGS = ["@openswe", "@open-swe", "@openswe-dev"]
_FACTS = GitHubDeliveryFacts(routable=True, allowed=True, auto_review=True, tags=_TAGS)
_REPOSITORY = {"name": "api", "owner": {"login": "acme"}}


def _facts(**changes: object) -> GitHubDeliveryFacts:
    return _FACTS.model_copy(update=changes)


def _issue(action: str = "opened", body: str = "@openswe fix it", **extra: object) -> dict:
    issue = {"number": 7, "title": "Crash", "body": body}
    return {"action": action, "issue": issue, "repository": _REPOSITORY, **extra}


def _comment(body: str, *, on_pr: bool, action: str = "created") -> dict:
    issue: dict[str, object] = {"number": 7, "title": "Crash", "body": ""}
    if on_pr:
        issue["pull_request"] = {"url": "https://api.github.com/repos/acme/api/pulls/7"}
    return {"action": action, "issue": issue, "comment": {"body": body}, "repository": _REPOSITORY}


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
            "merge_group",
            {"action": "checks_requested"},
            _facts(allowed=False),
            False,
            Route(("wake_merge_queue",)),
            id="merge-group",
        ),
        pytest.param(
            "pull_request",
            {"action": "opened"},
            _FACTS,
            True,
            Route(
                (
                    "wake_merge_queue",
                    "track_pull_request_state",
                    "track_pull_request_usage",
                    "review_pull_request",
                ),
                automations=True,
            ),
            id="pr-opened-by-the-agent",
        ),
        pytest.param(
            "pull_request",
            {"action": "ready_for_review"},
            _facts(allowed=False, auto_review=False),
            False,
            Route(
                (
                    "wake_merge_queue",
                    "track_pull_request_state",
                    "track_pull_request_usage",
                    "settle_human_reviews",
                )
            ),
            id="pr-ready-without-review",
        ),
        pytest.param(
            "pull_request",
            {"action": "synchronize"},
            _facts(auto_review=False),
            True,
            Route(
                (
                    "wake_merge_queue",
                    "track_pull_request_state",
                    "withdraw_superseded_approvals",
                    "notify_pr_updated",
                    "review_fork_push",
                    "track_pull_request_usage",
                    "settle_human_reviews",
                )
            ),
            id="pr-pushed",
        ),
        pytest.param(
            "pull_request",
            {"action": "closed"},
            _FACTS,
            False,
            Route(
                (
                    "wake_merge_queue",
                    "track_pull_request_state",
                    "track_pull_request_usage",
                    "settle_review_on_close",
                    "update_reviewer_watch",
                ),
                automations=True,
            ),
            id="pr-closed",
        ),
        pytest.param(
            "pull_request",
            {"action": "edited"},
            _FACTS,
            False,
            Route(("wake_merge_queue", "track_pull_request_state")),
            id="pr-edited",
        ),
        pytest.param(
            "push", {}, _FACTS, True, Route(("wake_merge_queue", "review_push")), id="push"
        ),
        pytest.param(
            "push",
            {},
            _facts(auto_review=False),
            False,
            Route(("wake_merge_queue",)),
            id="push-without-review",
        ),
        pytest.param(
            "check_run",
            {"action": "completed"},
            _FACTS,
            False,
            Route(("ci_event",)),
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
            Route(
                (
                    "wake_merge_queue",
                    "settle_human_reviews",
                    "record_completed_review",
                    "route_comment",
                )
            ),
            id="review-submitted",
        ),
        pytest.param(
            "pull_request_review",
            _review(body="@openswe address these"),
            _FACTS,
            True,
            Route(("wake_merge_queue", "settle_human_reviews", "record_completed_review")),
            id="the-agents-own-review",
        ),
        pytest.param(
            "pull_request_review",
            _review("dismissed"),
            _FACTS,
            False,
            Route(("wake_merge_queue", "settle_human_reviews")),
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
            Route(("route_comment",), mentioned=True),
            id="pr-comment-mention",
        ),
        pytest.param(
            "issue_comment",
            _comment("CI is failing", on_pr=True, action="edited"),
            _FACTS,
            False,
            Route(("route_comment",)),
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
            "pull_request_review_comment",
            {
                "action": "created",
                "comment": {"body": "The finding is wrong", "in_reply_to_id": 1},
                "repository": _REPOSITORY,
            },
            _FACTS,
            False,
            Route(("route_comment",)),
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
        self.planned = PlannedGitHubIssueRun(
            starts=True,
            run=GitHubIssueRun(
                input={"messages": [{"role": "user", "content": "Fix the crash"}]},
                context={"__run_token": "signed"},
                metadata={"channel": "github"},
                webhook="https://backend.example/webhooks/run-complete",
            ),
        )
        self.automations: list[GitHubAutomationRun] = []
        self.calls: list[tuple[str, BaseModel, str]] = []

    async def __call__(
        self, endpoint: Endpoint[BaseModel, BaseModel], request: BaseModel, *, msg_id: str
    ) -> BaseModel:
        self.calls.append((endpoint.name, request, msg_id))
        if endpoint is RECORD_GITHUB_DELIVERY:
            return self.facts
        assert endpoint is ACT_ON_GITHUB_DELIVERY
        act = cast(ActOnGitHubDelivery, request)
        return GitHubDeliveryActed(
            issue_run=self.planned if act.issue_run is not None else None,
            automations=self.automations if act.automations else [],
        )

    def called[Request: BaseModel](self, endpoint: Endpoint[Request, Any]) -> list[Request]:
        return [cast(Request, request) for name, request, _ in self.calls if name == endpoint.name]


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> _Backend:
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


async def test_an_issue_mention_starts_the_planned_run_on_the_issues_thread(
    backend: _Backend,
) -> None:
    started = await github.on_event(_event("issues", _issue()), _THREAD)

    assert started == [
        (
            {"messages": [{"role": "user", "content": "Fix the crash"}]},
            {
                "context": {"__run_token": "signed"},
                "metadata": {"channel": "github"},
                "webhook": "https://backend.example/webhooks/run-complete",
            },
        )
    ]
    # Two calls: log it and learn the facts, then act.
    assert [msg_id for *_, msg_id in backend.calls] == [
        "msg_delivery-1_record",
        "msg_delivery-1_act",
    ]
    [act] = backend.called(ACT_ON_GITHUB_DELIVERY)
    assert (act.work, act.automations) == ([], True)
    assert act.event == "issues"
    assert act.issue_run is not None
    assert (str(act.issue_run.thread_id), act.issue_run.waiting) == (_THREAD.id, False)


async def test_a_mention_while_the_thread_waits_only_points_to_the_decision(
    backend: _Backend,
) -> None:
    backend.planned = PlannedGitHubIssueRun(starts=True)
    waiting = GitHubChannelThread(id=_THREAD.id, channel=_THREAD.channel, interrupted=True)

    started = await github.on_event(
        _event("issue_comment", _comment("@openswe go", on_pr=False)), waiting
    )

    assert started == [(github.ACTS_WHILE_WAITING, {})]
    [act] = backend.called(ACT_ON_GITHUB_DELIVERY)
    assert act.issue_run is not None and act.issue_run.waiting is True


def _runs(started: GitHubEventResult) -> list[tuple[ChannelRunInput, ChannelRunOptions]]:
    """The runs a handler started, as a list."""
    assert isinstance(started, list)
    return started


def _automation(prompt: str) -> GitHubAutomationRun:
    return GitHubAutomationRun(
        thread_id=uuid.uuid4(),
        input={"messages": [{"role": "user", "content": prompt}]},
        context={"__run_token": f"signed:{prompt}"},
        metadata={"channel": "github"},
    )


async def test_each_automation_starts_on_the_thread_the_backend_planned(
    backend: _Backend,
) -> None:
    triage, label = _automation("Triage"), _automation("Label")
    backend.automations = [triage, label]

    started = _runs(await github.on_event(_event("issues", _issue()), _THREAD))

    # The mention's run on the issue's conversation, and each automation's on its own thread.
    assert len(started) == 3
    assert [options.get("thread") for _, options in started[1:]] == [
        {"mode": "persistent", "id": str(triage.thread_id)},
        {"mode": "persistent", "id": str(label.thread_id)},
    ]
    assert started[1] == (
        triage.input,
        {
            "context": triage.context,
            "metadata": triage.metadata,
            "thread": {"mode": "persistent", "id": str(triage.thread_id)},
        },
    )
    # Still two calls.
    assert len(backend.calls) == 2
    # MDA takes the list: one run on the conversation, the others on their threads.
    runs = normalize_run_inputs(started, RunChannel(name="github", provider="github"))
    assert ["thread" in run for run in runs] == [False, True, True]


async def test_automations_start_while_the_issues_thread_waits(backend: _Backend) -> None:
    backend.planned = PlannedGitHubIssueRun(starts=True)
    backend.automations = [_automation("Triage")]
    waiting = GitHubChannelThread(id=_THREAD.id, channel=_THREAD.channel, interrupted=True)

    started = _runs(await github.on_event(_event("issues", _issue()), waiting))

    assert started[0] == (github.ACTS_WHILE_WAITING, {})
    assert len(normalize_run_inputs(started, RunChannel(name="github", provider="github"))) == 2
    assert started[1][1].get("thread") == {
        "mode": "persistent",
        "id": str(backend.automations[0].thread_id),
    }


async def test_a_pull_request_opened_starts_only_its_automations(backend: _Backend) -> None:
    backend.automations = [_automation("Summarize")]

    started = _runs(await github.on_event(_event("pull_request", {"action": "opened"}), _THREAD))

    assert len(started) == 1
    [act] = backend.called(ACT_ON_GITHUB_DELIVERY)
    assert (act.automations, act.issue_run) == (True, None)


async def test_a_mention_the_backend_refuses_starts_nothing(backend: _Backend) -> None:
    backend.planned = PlannedGitHubIssueRun(starts=False)

    assert await github.on_event(_event("issues", _issue()), _THREAD) is None


async def test_pull_request_work_is_the_backends_and_starts_no_run_here(
    backend: _Backend,
) -> None:
    started = await github.on_event(
        _event("issue_comment", _comment("@openswe fix CI", on_pr=True)), _THREAD
    )

    assert started is None
    [act] = backend.called(ACT_ON_GITHUB_DELIVERY)
    assert (act.work, act.mentioned, act.issue_run) == (["route_comment"], True, None)


async def test_a_delivery_that_starts_nothing_is_only_logged(backend: _Backend) -> None:
    backend.facts = _facts(routable=False)

    assert await github.on_event(_event("issues", _issue()), _THREAD) is None
    assert [name for name, *_ in backend.calls] == [RECORD_GITHUB_DELIVERY.name]
