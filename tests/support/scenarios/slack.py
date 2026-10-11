"""Slack and the people in it, as a scenario boundary.

Everything Open SWE posts is kept, so a person can click a real button on a real message:
the click is the signed ``block_actions`` payload Slack would send, posted to Open SWE's own
interactivity route. A button that opens a modal is answered with the ``view_submission``
Slack would send. DMs, their edits and deletions become moments, labelled with the notice
kind Open SWE recorded for them in the store. Each Slack thread is owned by one agent thread,
and waking that owner hands the event to the scenario's agent.
"""

import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from pydantic import JsonValue

# Imported before any scenario patches anything: a module first imported mid-play would copy
# that play's fakes and keep them after the play ends.
from openswe.api.app import app
from openswe.slack import client as slack_client
from openswe.slack import dm, thread_owner
from openswe.slack.channels import SlackChannel
from openswe.users import User, UserIdentity
from openswe.utils import thread_ops
from openswe.webhooks import common
from tests.support.scenarios.core import (
    Fake,
    Person,
    Scenario,
    computing,
    returning,
    slack_plain,
)

_SIGNING_SECRET = "scenario-signing-secret"

type ChannelUpdate = Callable[[str, list[dict[str, JsonValue]] | None], str]
type Wake = Callable[[str, str], Awaitable[None]]


@dataclass
class Posted:
    """A message Open SWE posted, as it currently reads."""

    channel: str
    ts: str
    text: str
    blocks: list[dict[str, JsonValue]]

    def offers(self, action_id: str) -> bool:
        return self._find(action_id) is not None

    def button(self, action_id: str) -> dict[str, JsonValue]:
        if (element := self._find(action_id)) is None:
            raise AssertionError(
                f"no {action_id} button on {self.channel}/{self.ts}: {self.text[:80]}"
            )
        return element

    def _find(self, action_id: str) -> dict[str, JsonValue] | None:
        for block in self.blocks:
            elements = block.get("elements")
            for element in elements if isinstance(elements, list) else []:
                if isinstance(element, dict) and element.get("action_id") == action_id:
                    return element
        return None


class Directory:
    """Open SWE's users: one per person, linked to GitHub and Slack under the same login."""

    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.users = {
            login: User(
                identities=[
                    UserIdentity(provider="github", external_id=login, login=login),
                    UserIdentity(provider="slack", external_id=person.slack_id),
                ]
            )
            for login, person in scenario.people.items()
        }
        self.login_by_id = {user.id: login for login, user in self.users.items()}

    def __getitem__(self, login: str) -> User:
        return self.users[login]

    def fakes(self) -> list[tuple[object, Fake]]:
        return []

    def attribute_fakes(self) -> list[tuple[object, str, object]]:
        by_slack = {f"U_{login}": user for login, user in self.users.items()}
        return [
            (User, "for_login", computing(lambda _, login: self.users.get(str(login).lower()))),
            (
                User,
                "get",
                computing(lambda user_id: self.users.get(self.login_by_id.get(user_id, ""))),  # type: ignore[arg-type]
            ),
            (
                User,
                "for_person",
                computing(lambda person: by_slack.get(str(person["id"]).removeprefix("slack:"))),  # type: ignore[index]
            ),
            (User, "concierge_mode_for_slack", returning(False)),
        ]


class Slack:
    """Slack's API as Open SWE calls it, and Slack's interactivity webhook as people trigger it."""

    def __init__(
        self,
        scenario: Scenario,
        *,
        channels: dict[str, str],
        describe_update: ChannelUpdate | None = None,
    ) -> None:
        self.scenario = scenario
        self.channels = channels
        self.describe_update = describe_update
        self.posted: dict[str, Posted] = {}
        self.modals: list[dict[str, JsonValue]] = []
        self.wake: Wake | None = None
        self._last_update: dict[str, str] = {}
        scenario.langgraph.on_put.append(self._label)

    # What people do in Slack.

    async def click(self, person: Person, message: Posted, action_id: str) -> None:
        """``person`` clicks the ``action_id`` button on ``message``."""
        button = message.button(action_id)
        await self._deliver(
            {
                "type": "block_actions",
                "trigger_id": f"trigger-{self.scenario.next_seq()}",
                "user": {"id": person.slack_id},
                "channel": {"id": message.channel},
                "container": {
                    "type": "message",
                    "channel_id": message.channel,
                    "message_ts": message.ts,
                },
                "message": {"ts": message.ts, "text": message.text},
                "actions": [
                    {
                        "type": "button",
                        "action_id": action_id,
                        "value": button.get("value", ""),
                        "action_ts": f"{time.time():.6f}",
                    }
                ],
            }
        )

    async def choose(self, person: Person, choice: str) -> None:
        """``person`` picks ``choice`` in the modal Open SWE last opened and submits it."""
        if not self.modals:
            raise AssertionError("no modal is open")
        view = self.modals.pop()
        await self._deliver(
            {
                "type": "view_submission",
                "trigger_id": f"trigger-{self.scenario.next_seq()}",
                "user": {"id": person.slack_id},
                "view": {
                    "callback_id": view.get("callback_id", ""),
                    "private_metadata": view.get("private_metadata", ""),
                    "state": {
                        "values": {
                            "choice": {
                                "choice": {
                                    "type": "static_select",
                                    "selected_option": {
                                        "text": {"type": "plain_text", "text": choice},
                                        "value": choice,
                                    },
                                }
                            }
                        }
                    },
                },
            }
        )

    async def _deliver(self, payload: dict[str, JsonValue]) -> None:
        body = urlencode({"payload": json.dumps(payload)}).encode()
        timestamp = str(int(time.time()))
        digest = hmac.new(
            _SIGNING_SECRET.encode(), f"v0:{timestamp}:{body.decode()}".encode(), hashlib.sha256
        ).hexdigest()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://slack.test"
        ) as client:
            response = await client.post(
                "/webhooks/slack/interactivity",
                content=body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Slack-Request-Timestamp": timestamp,
                    "X-Slack-Signature": f"v0={digest}",
                },
            )
        response.raise_for_status()
        answer = response.json()
        if isinstance(answer, dict) and answer.get("status") not in (None, "accepted"):
            raise AssertionError(f"Slack's {payload['type']} was not handled: {answer}")

    # What Open SWE has posted.

    def last_dm(self, person: Person, labels: frozenset[str]) -> Posted:
        """The newest DM to ``person`` labelled one of ``labels``, as it reads now."""
        for moment in reversed(self.scenario.timeline):
            if (
                moment.kind == "message"
                and moment.target == person.login
                and moment.label in labels
            ):
                return self.posted[moment.ref]
        raise AssertionError(f"{person.login} has no DM labelled {sorted(labels)}")

    def message(self, channel: str, ts: str) -> Posted:
        return self.posted[f"{channel}/{ts}"]

    @staticmethod
    def thread_of(channel: str, ts: str) -> str:
        """The agent thread that owns a Slack thread."""
        return f"slack-thread-{channel}-{ts}"

    # The fakes.

    def _login(self, slack_or_dm: str) -> str:
        return slack_or_dm.removeprefix("U_").removeprefix("D_")

    def _label(self, namespace: tuple[str, ...], key: str, value: dict[str, JsonValue]) -> None:
        """Name a DM by the notice Open SWE recorded it as; a later re-save does not rename it."""
        notice = value.get("notice")
        if namespace[0] != "slack_dm_origin" or not isinstance(notice, dict):
            return
        for moment in self.scenario.timeline:
            if (
                moment.kind == "message"
                and moment.ref == f"{namespace[1]}/{key}"
                and not moment.label
            ):
                moment.label = str(notice.get("kind", ""))

    async def _post(
        self,
        channel_id: str,
        text: str,
        *,
        blocks: list[dict[str, JsonValue]] | None = None,
        **_: object,
    ) -> str:
        ts = f"{self.scenario.next_seq()}.0"
        self.posted[f"{channel_id}/{ts}"] = Posted(channel_id, ts, text, list(blocks or []))
        if channel_id.startswith("D_"):
            self.scenario.record(
                "message",
                slack_plain(text),
                target=self._login(channel_id),
                ref=f"{channel_id}/{ts}",
            )
        return ts

    async def _reply(self, channel_id: str, thread_ts: str, text: str, **kwargs: object) -> str:
        blocks = kwargs.get("blocks")
        return await self._post(
            channel_id, text, blocks=blocks if isinstance(blocks, list) else None
        )

    async def _update(
        self,
        channel_id: str,
        message_ts: str,
        text: str,
        *,
        blocks: list[dict[str, JsonValue]] | None = None,
        **_: object,
    ) -> None:
        ref = f"{channel_id}/{message_ts}"
        if ref in self.posted:
            self.posted[ref] = Posted(channel_id, message_ts, text, list(blocks or []))
        if channel_id.startswith("D_"):
            status = _context_text(blocks)
            if status and not status.startswith(":hourglass"):
                self.scenario.record(
                    "edit", slack_plain(status), target=self._login(channel_id), ref=ref
                )
            return
        if self.describe_update is None:
            return
        described = slack_plain(self.describe_update(channel_id, blocks))
        if described and self._last_update.get(channel_id) != described:
            self._last_update[channel_id] = described
            self.scenario.record("note", described)

    async def _delete(self, channel_id: str, message_ts: str) -> bool:
        self.posted.pop(f"{channel_id}/{message_ts}", None)
        self.scenario.record(
            "delete", "deleted", target=self._login(channel_id), ref=f"{channel_id}/{message_ts}"
        )
        return True

    async def _ephemeral(
        self, channel_id: str, user_id: str, text: str, thread_ts: str | None = None, **_: object
    ) -> bool:
        self.scenario.record("note", f"tells {self._login(user_id)}: {slack_plain(text)}")
        return True

    async def _open_modal(self, trigger_id: str, view: dict[str, JsonValue]) -> bool:
        self.modals.append(view)
        return True

    async def _wake_owner(
        self, channel_id: str, thread_ts: str, slack_user_id: str, event: str
    ) -> str:
        if self.wake is None:
            raise AssertionError("nothing listens for agent wake-ups in this scenario")
        thread_id = self.thread_of(channel_id, thread_ts)
        await self.wake(thread_id, event)
        return thread_id

    def _channel(self, reference: str) -> SlackChannel | None:
        name = reference.strip().removeprefix("#")
        channel_id = self.channels.get(name, name)
        if channel_id.startswith("D_"):
            payload: dict[str, JsonValue] = {"id": channel_id, "is_im": True}
        elif channel_id in self.channels.values():
            payload = {
                "id": channel_id,
                "name": next(n for n, c in self.channels.items() if c == channel_id),
                "is_channel": True,
                "is_member": True,
                "is_private": False,
                "is_ext_shared": False,
                "is_pending_ext_shared": False,
            }
        else:
            return None
        return SlackChannel.from_payload(payload)

    @asynccontextmanager
    async def _no_lock(self, *_: object, **__: object) -> AsyncIterator[None]:
        yield None

    def fakes(self) -> list[tuple[object, Fake]]:
        people = self.scenario.people
        return [
            (dm.open_dm, computing(lambda slack_user_id: f"D_{self._login(str(slack_user_id))}")),
            (slack_client.post_slack_top_level_message_with_ts, self._post),
            (slack_client.post_slack_thread_reply_with_ts, self._reply),
            (slack_client.update_slack_message, self._update),
            (slack_client.delete_slack_message, self._delete),
            (slack_client.post_slack_ephemeral_message, self._ephemeral),
            (slack_client.open_slack_modal, self._open_modal),
            (
                slack_client.get_slack_user_info,
                computing(lambda slack_id: {"tz": people[self._login(str(slack_id))].zone.key}),
            ),
            (
                slack_client.get_slack_permalink,
                computing(lambda channel, ts: f"https://slack.test/{channel}/{ts}"),
            ),
            (
                slack_client.lookup_slack_thread_id,
                computing(lambda _client, channel, ts: self.thread_of(str(channel), str(ts))),
            ),
            (slack_client.get_active_slack_thread, returning(None)),
            (slack_client.add_slack_reaction, returning(True)),
            (slack_client.remove_slack_reaction, returning(True)),
            (slack_client.slack_thread_mutation_lock, self._no_lock),
            (thread_ops.queue_message_for_thread, returning(True)),
            (thread_owner.wake_thread_owner, self._wake_owner),
        ]

    def attribute_fakes(self) -> list[tuple[object, str, object]]:
        return [
            (common, "SLACK_SIGNING_SECRET", _SIGNING_SECRET),
            (
                SlackChannel,
                "load",
                computing(lambda channel_id, **_: self._channel(str(channel_id))),
            ),
            (SlackChannel, "resolve", computing(lambda reference: self._channel(str(reference)))),
        ]


def _context_text(blocks: list[dict[str, JsonValue]] | None) -> str:
    for block in blocks or []:
        elements = block.get("elements")
        if block.get("type") == "context" and isinstance(elements, list) and elements:
            first = elements[0]
            if isinstance(first, dict) and isinstance(first.get("text"), str):
                return str(first["text"])
    return ""
