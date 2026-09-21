"""What the model receives as people come and go in a thread — executable spec.

Each turn renders through the real dispatch and prepare-run code, its "Then" is
asserted, and the dump of every message appended must match ``thread_context.md``
next to this file. Regenerate that fixture with ``UPDATE_THREAD_CONTEXT_FIXTURE=1``.
"""

import os
from pathlib import Path
from typing import Any, cast

from langchain_core.messages import HumanMessage

from agent.input_messages import (
    PersonIdentity,
    channel_introduction,
    human_input,
    person_introduction,
)
from agent.prompt import construct_sender_context
from agent.server import PrepareAgentRunMiddleware
from agent.utils.authorship import CollaboratorIdentity, ThreadParticipant

FIXTURE = Path(__file__).with_name("thread_context.md")

ALICE = "user:0199e0ae-1111-7000-8000-00000000a11c"
BOB = "user:0199e0ae-2222-7000-8000-000000000b0b"
CAROL = "slack:U0CAR0L"
CHANNEL = "slack:C0BQUH14FK3"

alice = CollaboratorIdentity(
    display_name="Alice",
    commit_name="Alice",
    commit_email="alice@users.noreply.github.com",
    github_login="alice",
)
bob = CollaboratorIdentity(
    display_name="Bob",
    commit_name="Bob",
    commit_email="bob@users.noreply.github.com",
    github_login="bob",
)
carol = CollaboratorIdentity(
    display_name="Carol", commit_name="Carol", commit_email="carol@example.com"
)
P_ALICE = ThreadParticipant(identity=alice, person_id=ALICE, workspace_admin=True)
P_BOB = ThreadParticipant(identity=bob, person_id=BOB, draft_prs=False)
P_BOB_INSTRUCTED = ThreadParticipant(
    identity=bob,
    person_id=BOB,
    draft_prs=False,
    instructions="Never use ripgrep.\nRun `make lint` before every push.",
)
P_CAROL = ThreadParticipant(identity=carol, person_id=CAROL)


class Thread:
    """One thread accumulating state the way dispatch and the run append to it."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {"messages": []}
        self.introduced: set[str] = set()
        self.dump: list[str] = []

    def _append(self, content: str) -> None:
        self.state["messages"].append(HumanMessage(content=content))
        self.dump.append(f"```xml\n{content}\n```\n")

    def turn(
        self,
        *,
        title: str,
        given: str,
        when: str,
        then: str,
        person_id: str,
        person: PersonIdentity | None,
        text: str,
        ts: str,
        surface: str,
        display_name: str,
        participants: list[ThreadParticipant],
    ) -> dict[str, Any]:
        self.dump.append(f"## {title}\nGiven: {given}\nWhen: {when}\nThen: {then}\n")
        self.dump.append("### dispatch appends")
        introduced: list[str] = []
        if CHANNEL not in self.introduced:
            block = channel_introduction(
                {"id": CHANNEL, "platform": "slack", "name": "open-swe-dev"}
            )
            self.introduced.add(CHANNEL)
            self._append(cast(str, block["content"]))
        if person is not None:
            content = cast(str, person_introduction(person)["content"])
            if content not in self.introduced:
                self.introduced.add(content)
                introduced.append(content)
                self._append(content)
        context: dict[str, Any] = {"sender_id": person_id, "surface": surface, "kind": "human"}
        if surface == "slack":
            context["channel_id"] = CHANNEL
            context["data"] = {"timestamp": ts}
        envelope = cast(str, human_input(text, context)["content"])
        self._append(envelope)

        self.dump.append("### run appends")
        participant_msgs = PrepareAgentRunMiddleware._participants_messages(
            cast(Any, self.state), participants
        )
        pointer_msgs = PrepareAgentRunMiddleware._sender_context_messages(
            construct_sender_context(display_name, person_id)
        )
        if not participant_msgs:
            self.dump.append("(participants: all already visible, none re-sent)\n")
        for message in [*participant_msgs, *pointer_msgs]:
            self._append(cast(str, message["content"]))
        return {
            "introduced": introduced,
            "envelope": envelope,
            "participants": [cast(str, m["content"]) for m in participant_msgs],
            "pointer": cast(str, pointer_msgs[-1]["content"]),
        }


def walkthrough() -> tuple[str, list[dict[str, Any]]]:
    thread = Thread()
    thread.dump.append(
        "# Thread context dump\n"
        "Every message appended per turn, rendered by test_thread_context.py through "
        "the real renderers. Regenerate with UPDATE_THREAD_CONTEXT_FIXTURE=1.\n"
    )
    turns: list[dict[str, Any]] = []

    t1 = thread.turn(
        title="Turn 1: Alice starts the thread from Slack",
        given="an empty thread; Alice has a linked GitHub account and is a workspace admin",
        when="she mentions the bot: add a greet() helper",
        then="channel and Alice introduced; Alice's participant block sent; pointer names Alice",
        person_id=ALICE,
        person={
            "id": ALICE,
            "platform": "slack",
            "display_name": "Alice",
            "github_login": "alice",
            "email": "alice@example.com",
            "timezone": "America/New_York",
            "open_swe_account": "linked",
        },
        text="add a greet() helper",
        ts="1789991539.477079",
        surface="slack",
        display_name="Alice",
        participants=[P_ALICE],
    )
    assert len(t1["participants"]) == 1 and f'id="{ALICE}"' in t1["participants"][0]
    assert 'timestamp="1789991539.477079"' in t1["envelope"]
    assert "Sent by **Alice**" in t1["pointer"]
    turns.append(t1)

    t2 = thread.turn(
        title="Turn 2: Alice follows up",
        given="turn 1 has run to completion",
        when="Alice replies in the same Slack thread: also add a docstring",
        then="only her envelope and the pointer; nothing about her has changed",
        person_id=ALICE,
        person=None,
        text="also add a docstring",
        ts="1789992088.489369",
        surface="slack",
        display_name="Alice",
        participants=[P_ALICE],
    )
    assert t2["participants"] == []
    assert "Sent by **Alice**" in t2["pointer"]
    turns.append(t2)

    t3 = thread.turn(
        title="Turn 3: Bob joins",
        given="Bob has a linked account and prefers PRs opened ready for review",
        when="Bob replies: make it return bytes",
        then="Bob introduced; only Bob's participant block sent, Alice's is not repeated; pointer names Bob",
        person_id=BOB,
        person={
            "id": BOB,
            "platform": "slack",
            "display_name": "Bob",
            "github_login": "bob",
            "open_swe_account": "linked",
        },
        text="make it return bytes",
        ts="1789992258.131819",
        surface="slack",
        display_name="Bob",
        participants=[P_ALICE, P_BOB],
    )
    assert len(t3["participants"]) == 1 and f'id="{BOB}"' in t3["participants"][0]
    assert "<new_prs>ready for review</new_prs>" in t3["participants"][0]
    turns.append(t3)

    t4 = thread.turn(
        title="Turn 4: Alice switches to the web dashboard",
        given="the thread has Alice and Bob",
        when="Alice types in the dashboard: ship it",
        then="same user: id as her Slack turns; no participant block re-sent; her person block reappears once with the dashboard's attributes",
        person_id=ALICE,
        person={
            "id": ALICE,
            "platform": "github",
            "github_login": "alice",
            "email": "alice@example.com",
        },
        text="ship it",
        ts="",
        surface="web",
        display_name="Alice",
        participants=[P_ALICE, P_BOB],
    )
    assert t4["participants"] == []
    assert f'sender="{ALICE}"' in t4["envelope"] and 'surface="web"' in t4["envelope"]
    turns.append(t4)

    t5 = thread.turn(
        title="Turn 5: Bob sets standing instructions, then asks for the PR",
        given="Bob saved personal instructions between turns",
        when="Bob replies: open the PR",
        then="only Bob's participant block is re-sent, now with his instructions; pointer names Bob",
        person_id=BOB,
        person=None,
        text="open the PR",
        ts="1789992400.000001",
        surface="slack",
        display_name="Bob",
        participants=[P_ALICE, P_BOB_INSTRUCTED],
    )
    assert len(t5["participants"]) == 1 and f'id="{BOB}"' in t5["participants"][0]
    assert "Never use ripgrep." in t5["participants"][0]
    turns.append(t5)

    t6 = thread.turn(
        title="Turn 6: Carol, with no Open SWE account, chimes in",
        given="Carol never signed in to Open SWE",
        when="she replies: can it handle unicode?",
        then="keyed by her Slack id, marked unlinked; only her participant block sent, with only what Slack knows",
        person_id=CAROL,
        person={
            "id": CAROL,
            "platform": "slack",
            "display_name": "Carol",
            "open_swe_account": "unlinked",
        },
        text="can it handle unicode?",
        ts="1789992500.000001",
        surface="slack",
        display_name="Carol",
        participants=[P_ALICE, P_BOB_INSTRUCTED, P_CAROL],
    )
    assert len(t6["participants"]) == 1 and f'id="{CAROL}"' in t6["participants"][0]
    assert any("<open_swe_account>unlinked</open_swe_account>" in c for c in t6["introduced"])
    turns.append(t6)

    return "\n".join(thread.dump), turns


def test_thread_context_matches_fixture() -> None:
    rendered, turns = walkthrough()
    assert len(turns) == 6
    if os.environ.get("UPDATE_THREAD_CONTEXT_FIXTURE"):
        FIXTURE.write_text(rendered)
    assert rendered == FIXTURE.read_text(), (
        "thread_context.md is out of date; rerun with UPDATE_THREAD_CONTEXT_FIXTURE=1"
    )
