"""Deliver a GitHub event as MDA's GitHub channel does, against the real app.

MDA's handler (``mda/rt/github.py``) logs each delivery on the backend, routes it
with ``mda/rt/github_routes.py``, and asks the backend to run the work it routes
to, and to plan the runs of its automations and an issue's. This does the same, each call signed with the
service secret as MDA signs it. The backend runs its work after answering, which
an in-process client waits for, so a test sees its effects once this returns.
"""

import importlib.util
import json
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
    RECORD_GITHUB_DELIVERY,
    ActOnGitHubDelivery,
    GitHubDelivery,
    GitHubDeliveryFacts,
    GitHubIssueRunRequest,
    GitHubThreadRun,
    PlannedGitHubIssueRun,
)
from standardwebhooks import Webhook
from starlette.types import ASGIApp

if TYPE_CHECKING:
    # The E2E harness imports this module without the dev extras.
    import pytest

SERVICE_SECRET = "6d" * 32  # noqa: S105 - test value


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
    # Whether the delivery may fire automations, and the runs the backend planned for them.
    automations: bool = False
    automation_runs: tuple[GitHubThreadRun, ...] = ()
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
        facts = await _call(
            client, RECORD_GITHUB_DELIVERY, delivery, f"msg_{delivery_id}_record", secret
        )
        routed = MDA_ROUTES.route(event_type, delivery.payload, facts, from_app=from_app)
        planned = None
        automation_runs: list[GitHubThreadRun] = []
        comment_run = None
        if routed.work or routed.comment or routed.automations or routed.issue_run:
            issue_run = (
                GitHubIssueRunRequest(thread_id=thread_id or uuid.uuid4(), waiting=waiting)
                if routed.issue_run
                else None
            )
            acted = await _call(
                client,
                ACT_ON_GITHUB_DELIVERY,
                ActOnGitHubDelivery(
                    **delivery.model_dump(),
                    work=list(routed.work),
                    comment=routed.comment,
                    mentioned=routed.mentioned,
                    automations=routed.automations,
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
        work=tuple(routed.work),
        issue_run=planned,
        automations=routed.automations,
        automation_runs=tuple(automation_runs),
        comment=routed.comment,
        comment_run=comment_run,
    )


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
