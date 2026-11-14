"""What a plugin with ``threads:read`` reads of agent threads, and what it never does."""

import json
from datetime import UTC, datetime
from typing import Literal
from unittest.mock import AsyncMock
from uuid import uuid7

import pytest
from fastapi import HTTPException

from openswe.plugins import routes as plugin_routes
from openswe.plugins import threads as plugin_threads
from openswe.plugins.installed import InstalledPlugin, PluginPermissionDenied
from openswe.plugins.records import PluginApps, PluginInstallations
from openswe.plugins.threads import BotModel, PluginThreads
from openswe.slack.allowed_bots import AllowedSlackBotEntry
from openswe.threads import index, plan_store
from openswe.threads.runtimes import RUNTIMES
from openswe.threads.summary import PLUGIN_CHAT_KEY, SLACK_BOT_TRIGGER_KIND, TRIGGERING_BOT_KEY
from openswe.transcript.engine import Command, append
from openswe.transcript.events import (
    MessageCompleted,
    MessageSender,
    ThreadCreated,
    TurnRequested,
)
from openswe.users import User

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture(autouse=True)
def _authorized_logins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "ada,bob")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")
    monkeypatch.setenv("CONFIGURED_ADMINS", "")


def _bundle(permissions: list[str]) -> str:
    manifest = {
        "name": "incidents",
        "displayName": "Incidents",
        "version": "1.0.0",
        "sidebar": {"label": "Incidents"},
        "permissions": permissions,
    }
    return f'<script type="application/openswe-plugin+json">{json.dumps(manifest)}</script>'


async def _plugin(login: str, external_id: str, permissions: list[str]) -> InstalledPlugin:
    user = await User.sign_in("github", external_id, login=login)
    version = await PluginApps.publish(user.id, _bundle(permissions), origin={})
    installation = await PluginInstallations.install(version, user_id=user.id)
    return InstalledPlugin(
        installation=await PluginInstallations.get(installation, user.id),
        user=user,
        email=None,
        login=login,
    )


def _incident(**overrides: object) -> dict[str, object]:
    return {
        "source": "incidents_agent",
        "visibility": "public",
        "owner_type": "system",
        "title": "#inc-42-db",
        "source_context": {"slack_thread": {"channel_id": "C42", "thread_ts": "5.0"}},
        "incident_channel": {"id": "C42", "name": "inc-42-db"},
        **overrides,
    }


async def _thread(thread_id: str, metadata: dict[str, object]) -> None:
    await index.create(thread_id, runtime="local", metadata=metadata)


async def _listed(plugin: InstalledPlugin, source: str) -> list[str]:
    page = await plugin_routes.api_plugin_threads(plugin, source)
    return sorted(thread.thread_id for thread in page.threads)


async def test_a_plugin_reads_no_thread_without_threads_read() -> None:
    plugin = await _plugin("ada", "1", ["storage"])
    await _thread("inc-1", _incident())

    with pytest.raises(PluginPermissionDenied):
        _ = plugin.threads
    calls = [
        plugin_routes.api_plugin_threads(plugin, "incidents_agent"),
        plugin_routes.api_plugin_thread("inc-1", plugin),
        plugin_routes.api_plugin_thread_messages("inc-1", plugin),
        plugin_routes.api_plugin_thread_artifact("inc-1", plugin),
    ]
    for call in calls:
        with pytest.raises(HTTPException) as refused:
            await call
        assert refused.value.status_code == 403


async def test_incident_threads_list_for_everyone_and_others_as_the_dashboard_reads_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A thread the index lacks is looked up on the Agent Server, which knows none here.
    monkeypatch.setattr(type(RUNTIMES.local), "indexed", AsyncMock(return_value=None))
    ada = await _plugin("ada", "1", ["threads:read"])
    bob = await _plugin("bob", "2", ["threads:read"])
    await _thread("inc-public", _incident())
    # Carrying the incident source alone opens nothing the dashboard keeps closed.
    await _thread("inc-private", _incident(visibility="private", owner_login="ada"))
    await _thread("inc-owned", _incident(owner_type="user", owner_login="ada"))
    await _thread("slack-public", {"source": "slack", "visibility": "public"})
    await _thread("slack-adas", {"source": "slack", "visibility": "private", "owner_login": "ada"})
    await _thread(
        "slack-chat",
        {"source": "slack", "visibility": "public", PLUGIN_CHAT_KEY: "install-1"},
    )

    for plugin in (ada, bob):
        assert await _listed(plugin, "incidents_agent") == ["inc-public"]
    assert await _listed(ada, "slack") == ["slack-adas", "slack-public"]
    assert await _listed(bob, "slack") == ["slack-public"]

    summary = await plugin_routes.api_plugin_thread("inc-public", bob)
    assert summary.source == "incidents_agent"
    # The dashboard won't open it, so the plugin gets no link that would fail.
    assert summary.url is None
    assert summary.slack is not None
    assert (summary.slack.channel_id, summary.slack.channel_name) == ("C42", "inc-42-db")
    for thread_id in ("inc-private", "inc-owned", "slack-adas", "missing"):
        with pytest.raises(HTTPException) as hidden:
            await plugin_routes.api_plugin_thread(thread_id, bob)
        assert hidden.value.status_code == 404


async def test_bot_filter_keeps_one_bots_threads_named_from_the_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bob = await _plugin("bob", "2", ["threads:read"])
    monkeypatch.setattr(
        plugin_threads,
        "allowed_slack_bot_directory",
        AsyncMock(return_value=[AllowedSlackBotEntry(key="T1:B1", name="Alerts")]),
    )
    for thread_id, bot in (("alerts", "T1:B1"), ("retired", "T1:B2")):
        await _thread(
            thread_id,
            {
                "source": "slack",
                "visibility": "public",
                "trigger_kind": SLACK_BOT_TRIGGER_KIND,
                TRIGGERING_BOT_KEY: bot,
            },
        )
    await _thread("person", {"source": "slack", "visibility": "public"})

    one = await plugin_routes.api_plugin_threads(bob, "slack", bot="T1:B1")
    assert [(t.thread_id, t.bot) for t in one.threads] == [
        ("alerts", BotModel(key="T1:B1", name="Alerts"))
    ]
    every = await plugin_routes.api_plugin_threads(bob, "slack", bot="*")
    assert sorted((t.thread_id, t.bot.name if t.bot else "") for t in every.threads) == [
        ("alerts", "Alerts"),
        ("retired", None),
    ]


async def test_a_page_short_of_unreadable_threads_still_points_past_them() -> None:
    bob = await _plugin("bob", "2", ["threads:read"])
    for n in range(3):
        await _thread(
            f"slack-{n}", {"source": "slack", "visibility": "private", "owner_login": "ada"}
        )

    page = await plugin_routes.api_plugin_threads(bob, "slack", limit=2)

    assert page.threads == []
    assert page.next_offset == 2
    last = await plugin_routes.api_plugin_threads(bob, "slack", limit=2, offset=2)
    assert last.next_offset is None


async def _transcript(thread_id: str) -> None:
    """A turn with a person's message, a subagent's aside, and the agent's answer."""
    turn = uuid7()
    message = MessageSender(login="system:incidents", kind="system", display_name="Incidents")

    def said(message_id: str, role: Literal["ai"], text: str, namespace: list[str]) -> Command:
        return Command(
            command_id=f"message:{message_id}",
            event=MessageCompleted(
                turn_id=turn,
                message_id=message_id,
                role=role,
                text=text,
                namespace=namespace,
                created_at=datetime.now(UTC),
            ),
            actor_kind="agent",
            turn_id=turn,
        )

    await append(
        thread_id,
        [
            Command(
                command_id=f"thread:{thread_id}",
                event=ThreadCreated(
                    title="#inc-42-db",
                    source="incidents_agent",
                    owner_login="",
                    visibility="public",
                    metadata={},
                ),
                actor_kind="system",
            ),
            Command(
                command_id=f"turn:{turn}",
                event=TurnRequested(
                    turn_id=turn, message_id="m1", text="The API is returning 500s", sender=message
                ),
                actor_kind="system",
                turn_id=turn,
            ),
        ],
    )
    # The agent answers after the request lands, in a later write, as it does live.
    await append(
        thread_id,
        [said("m2", "ai", "grep says nothing", ["task:1"]), said("m3", "ai", "Root cause.", [])],
    )


async def test_messages_and_artifact_read_only_as_their_person_may(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bob = await _plugin("bob", "2", ["threads:read"])
    await _thread("inc-public", _incident())
    await _thread("slack-adas", {"source": "slack", "visibility": "private", "owner_login": "ada"})
    await _transcript("inc-public")
    monkeypatch.setattr(plan_store, "_merge_thread_metadata", AsyncMock())
    await plan_store.save_plan_content("inc-public", html="<h1>Report</h1>")

    read = await plugin_routes.api_plugin_thread_messages("inc-public", bob)
    assert read is not None
    assert [(m.role, m.text, m.sender_id) for m in read.messages] == [
        ("user", "The API is returning 500s", "system:incidents"),
        ("assistant", "Root cause.", None),
    ]
    newest = await plugin_routes.api_plugin_thread_messages("inc-public", bob, limit=1)
    assert newest is not None
    assert ([m.text for m in newest.messages], newest.omitted) == (["Root cause."], 1)
    artifact = await plugin_routes.api_plugin_thread_artifact("inc-public", bob)
    assert artifact is not None
    assert (artifact.format, artifact.content) == ("html", "<h1>Report</h1>")

    # A thread from before the transcript log says so instead of reading as empty.
    await _thread("inc-old", _incident())
    assert await plugin_routes.api_plugin_thread_messages("inc-old", bob) is None

    for read_one in (
        plugin_routes.api_plugin_thread_messages("slack-adas", bob),
        plugin_routes.api_plugin_thread_artifact("slack-adas", bob),
    ):
        with pytest.raises(HTTPException) as hidden:
            await read_one
        assert hidden.value.status_code == 404


def test_only_system_owned_public_incident_threads_widen_the_dashboard() -> None:
    bob = PluginThreads(login="bob", email=None)
    assert bob.may_read(_incident())
    assert not bob.may_read(_incident(visibility="private"))
    assert not bob.may_read(_incident(owner_type="user"))
    # The same thread with any other source the dashboard does not surface stays closed.
    assert not bob.may_read(_incident(source="review"))


async def test_threads_without_a_source_list_as_dashboard_threads() -> None:
    bob = await _plugin("bob", "2", ["threads:read"])
    await _thread("dash-old", {"visibility": "public"})
    await _thread("dash-new", {"source": "dashboard", "visibility": "public"})
    await _thread("slack-public", {"source": "slack", "visibility": "public"})
    assert sorted(await _listed(bob, "dashboard")) == ["dash-new", "dash-old"]


@pytest.mark.parametrize(
    "index_status,run_status,shown",
    [
        # The index settled the run; the run metadata hasn't caught up.
        ("idle", "pending", "idle"),
        ("idle", "running", "idle"),
        ("busy", "pending", "running"),
        ("idle", "success", "finished"),
        ("error", None, "error"),
    ],
)
async def test_status_follows_the_index_over_stale_run_metadata(
    index_status: str, run_status: str | None, shown: str
) -> None:
    bob = await _plugin("bob", "2", ["threads:read"])
    metadata = _incident() if run_status is None else _incident(latest_run_status=run_status)
    await index.create("inc-1", runtime="local", metadata=metadata, status=index_status)
    assert (await plugin_routes.api_plugin_thread("inc-1", bob)).status == shown
