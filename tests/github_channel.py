"""Deliver a GitHub event as MDA's GitHub channel does, against the real app.

MDA's channel forwards each delivery to the backend, which logs it and picks and
runs its own work; for a delivery that may start an agent run, its handler
(``mda/rt/github.py``) asks for the repository's facts, routes the delivery with
``mda/rt/github_routes.py``, asks for the repository's automations and, for an issue
mention, who is an Open SWE user, writes the runs' prompts, and asks the backend to
sign the runs. This does the same,
each call signed with the service secret as MDA signs it, but writes a stand-in
for each prompt (``mda/tests`` hold MDA's to the backend's). The backend runs its
work after answering, which an in-process client waits for, so a test sees its
effects once this returns.
"""

import importlib.util
import json
import re
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel
from runtime_api.endpoint import Endpoint
from runtime_api.github import (
    ACT_ON_GITHUB_DELIVERY,
    GITHUB_AUTOMATION_RULES,
    GITHUB_DELIVERY_FACTS,
    GITHUB_ISSUE_FACTS,
    LOGIN_PATTERN,
    REPOSITORY_PATTERN,
    ActOnGitHubDelivery,
    FiredGitHubAutomation,
    GitHubAutomationRulesRequest,
    GitHubDelivery,
    GitHubDeliveryFacts,
    GitHubIssueFactsRequest,
    GitHubIssueRunRequest,
    GitHubThreadRun,
    GitHubTriggerEvent,
    PlannedGitHubIssueRun,
)
from standardwebhooks import Webhook
from starlette.types import ASGIApp

from openswe.github.work import delivery_work

if TYPE_CHECKING:
    # The E2E harness imports this module without the dev extras.
    import pytest

SERVICE_SECRET = "6d" * 32  # noqa: S105 - test value
# The stand-in for the input MDA writes for an issue mention's run.
ISSUE_RUN_INPUT: dict[str, Any] = {"messages": [{"role": "user", "content": "The issue"}]}


def _mda_routes() -> ModuleType:
    """MDA's switchboard; standard library and ``runtime_api`` only, so the backend loads it."""
    path = Path(__file__).parents[1] / "mda" / "rt" / "github_routes.py"
    spec = importlib.util.spec_from_file_location("mda_github_routes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MDA_ROUTES = _mda_routes()


@dataclass(frozen=True)
class GitHubDelivered:
    """What MDA's channel did with a delivery."""

    facts: GitHubDeliveryFacts
    # The backend's work the delivery started, in order.
    work: tuple[str, ...]
    # The issue run the backend planned; ``None`` when the delivery routes to none.
    issue_run: PlannedGitHubIssueRun | None
    # Whether the delivery may fire automations, the ones the repository's rules
    # matched, and the runs the backend planned for them.
    automations: bool = False
    automation_runs: tuple[GitHubThreadRun, ...] = ()
    fired: tuple[FiredGitHubAutomation, ...] = ()
    # Whether the delivery is a pull request comment to route, and the run the
    # backend planned for it on the pull request's own thread.
    comment: bool = False
    comment_run: GitHubThreadRun | None = None


async def _call[Request: BaseModel, Response: BaseModel](
    client: httpx.AsyncClient,
    endpoint: Endpoint[Request, Response],
    request: Request,
    msg_id: str,
    secret: str,
) -> Response:
    content = request.model_dump_json()
    timestamp = datetime.now(tz=UTC)
    response = await client.post(
        endpoint.path,
        content=content,
        headers={
            "content-type": "application/json",
            "webhook-id": msg_id,
            "webhook-timestamp": str(int(timestamp.timestamp())),
            "webhook-signature": Webhook(secret).sign(msg_id, timestamp, content),
        },
    )
    response.raise_for_status()
    return endpoint.response.model_validate(response.json())


async def call_as_mda[Request: BaseModel, Response: BaseModel](
    app: ASGIApp, endpoint: Endpoint[Request, Response], request: Request, *, secret: str
) -> Response:
    """One call of MDA's channel on ``app``, signed with ``secret``."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await _call(client, endpoint, request, f"msg_{uuid.uuid4()}", secret)


async def forward_as_mda(
    client: httpx.AsyncClient, delivery: GitHubDelivery, secret: str
) -> httpx.Response:
    """Forward a delivery as MDA's channel does: the body and GitHub's headers, signed
    with ``secret``."""
    content = json.dumps(delivery.payload)
    msg_id = f"msg_{delivery.delivery_id}_forward"
    timestamp = datetime.now(tz=UTC)
    response = await client.post(
        "/mda/github/deliveries",
        content=content,
        headers={
            "content-type": "application/json",
            "x-github-event": delivery.event,
            "x-github-delivery": delivery.delivery_id,
            "webhook-id": msg_id,
            "webhook-timestamp": str(int(timestamp.timestamp())),
            "webhook-signature": Webhook(secret).sign(msg_id, timestamp, content),
        },
    )
    response.raise_for_status()
    return response


async def deliver_as_mda(
    app: ASGIApp,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    secret: str,
    delivery_id: str | None = None,
    from_app: bool = False,
    thread_id: uuid.UUID | None = None,
    waiting: bool = False,
) -> GitHubDelivered:
    """Deliver ``payload`` as GitHub's ``event_type`` through MDA's channel to ``app``.

    ``secret``: the service secret the backend verifies. ``from_app``: Open SWE's own
    App caused it. ``thread_id`` and ``waiting``: the issue's conversation thread, and
    whether it waits on an interrupt.
    """
    delivery_id = delivery_id or f"delivery-{uuid.uuid4()}"
    delivery = GitHubDelivery(
        event=event_type,
        delivery_id=delivery_id,
        payload=json.loads(json.dumps(payload)),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # The channel forwards every delivery first, as GitHub sent it.
        await forward_as_mda(client, delivery, secret)
        # The facts, which the channel asks for only for deliveries that may start an
        # agent run; read here for every one, for the test to see.
        facts = await _call(
            client, GITHUB_DELIVERY_FACTS, delivery, f"msg_{delivery_id}_facts", secret
        )
        action = delivery.payload.get("action")
        key = event_type if action is None else f"{event_type}.{action}"
        routed = (
            MDA_ROUTES.route(event_type, delivery.payload, facts, from_app=from_app)
            if key in MDA_ROUTES.AGENT_EVENT_KEYS
            else MDA_ROUTES.Route()
        )
        fired = (
            await _fired(client, event_type, delivery.payload, delivery_id, secret)
            if routed.automations
            else []
        )
        issue_run = (
            await _issue_run(
                client, delivery.payload, delivery_id, secret, thread_id or uuid.uuid4(), waiting
            )
            if routed.issue_run
            else None
        )
        planned = None
        automation_runs: list[GitHubThreadRun] = []
        comment_run = None
        if routed.comment or fired or issue_run:
            acted = await _call(
                client,
                ACT_ON_GITHUB_DELIVERY,
                ActOnGitHubDelivery(
                    **delivery.model_dump(),
                    comment=routed.comment,
                    mentioned=routed.mentioned,
                    automations=fired,
                    issue_run=issue_run,
                ),
                f"msg_{delivery_id}_act",
                secret,
            )
            planned = acted.issue_run
            automation_runs = acted.automations
            comment_run = acted.comment_run
    return GitHubDelivered(
        facts=facts,
        # The backend picked and ran it as it took the forwarded delivery.
        work=delivery_work(event_type, delivery.payload, facts),
        issue_run=planned,
        automations=routed.automations,
        automation_runs=tuple(automation_runs),
        fired=tuple(fired),
        comment=routed.comment,
        comment_run=comment_run,
    )


def _fields(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _trigger_events(event_type: str, payload: Mapping[str, Any]) -> set[GitHubTriggerEvent]:
    action = payload.get("action")
    if event_type == "issues":
        return {"issues.opened"} if action == "opened" else set()
    if action == "opened":
        return {"pull_request.opened"}
    if action != "closed":
        return set()
    if _fields(payload.get("pull_request")).get("merged") is True:
        return {"pull_request.closed", "pull_request.merged"}
    return {"pull_request.closed"}


async def _fired(
    client: httpx.AsyncClient,
    event_type: str,
    payload: Mapping[str, Any],
    delivery_id: str,
    secret: str,
) -> list[FiredGitHubAutomation]:
    """The repository's automations the delivery fires, as MDA matches them."""
    events = _trigger_events(event_type, payload)
    repository = _fields(payload.get("repository"))
    full_name = f"{_fields(repository.get('owner')).get('login')}/{repository.get('name')}"
    if not events or not re.fullmatch(REPOSITORY_PATTERN, full_name):
        return []
    subject = _fields(payload.get("issue" if event_type == "issues" else "pull_request"))
    rules = await _call(
        client,
        GITHUB_AUTOMATION_RULES,
        GitHubAutomationRulesRequest(
            repository=full_name,
            author=str(_fields(subject.get("user")).get("login") or ""),
        ),
        f"msg_{delivery_id}_automations",
        secret,
    )
    fired: list[FiredGitHubAutomation] = []
    for automation in rules.automations:
        matched = sorted(events & set(automation.events))
        if matched:
            event = "pull_request.merged" if "pull_request.merged" in matched else matched[0]
            fired.append(
                FiredGitHubAutomation(
                    automation_id=automation.id, event=event, prompt=automation.prompt
                )
            )
    return fired


async def _issue_run(
    client: httpx.AsyncClient,
    payload: Mapping[str, Any],
    delivery_id: str,
    secret: str,
    thread_id: uuid.UUID,
    waiting: bool,
) -> GitHubIssueRunRequest:
    """The issue mention's run, as MDA asks the backend to sign it; the backend checks
    the sender itself, so this asks it whatever the facts say."""
    if waiting:
        return GitHubIssueRunRequest(thread_id=thread_id, waiting=True)
    sender = str(_fields(payload.get("sender")).get("login") or "")
    workspace = None
    if re.fullmatch(LOGIN_PATTERN, sender):
        facts = await _call(
            client,
            GITHUB_ISSUE_FACTS,
            GitHubIssueFactsRequest(thread_id=thread_id, sender=sender, logins=[sender]),
            f"msg_{delivery_id}_issue",
            secret,
        )
        workspace = facts.thread_workspace if facts.conversation_started else None
    return GitHubIssueRunRequest(thread_id=thread_id, input=ISSUE_RUN_INPUT, workspace=workspace)


async def deliver_github_event(
    monkeypatch: pytest.MonkeyPatch,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    delivery_id: str | None = None,
    from_app: bool = False,
    thread_id: uuid.UUID | None = None,
    waiting: bool = False,
) -> GitHubDelivered:
    """``deliver_as_mda`` to the real app, with the service secret it verifies."""
    from openswe.api.app import app

    monkeypatch.setenv("MDA_SERVICE_SECRET", SERVICE_SECRET)
    return await deliver_as_mda(
        app,
        event_type,
        payload,
        secret=SERVICE_SECRET,
        delivery_id=delivery_id,
        from_app=from_app,
        thread_id=thread_id,
        waiting=waiting,
    )
