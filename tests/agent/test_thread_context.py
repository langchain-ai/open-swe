"""What the model receives as people come and go in a thread — executable spec.

Each turn renders through the real dispatch and prepare-run code, its "Then" is
asserted, and the dump of every message appended must match ``thread_context.md``
next to this file. Regenerate that fixture with ``UPDATE_THREAD_CONTEXT_FIXTURE=1``.
"""

import os
from pathlib import Path
from typing import Any, cast

from langchain_core.messages import HumanMessage

from agent.input_messages import channel_introduction, human_input
from agent.server import PrepareAgentRunMiddleware
from agent.utils.authorship import CollaboratorIdentity, ThreadParticipant

FIXTURE = Path(__file__).with_name("thread_context.md")

ALICE = "user:0199e0ae-1111-7000-8000-00000000a11c"
BOB = "user:0199e0ae-2222-7000-8000-000000000b0b"
CAROL = "slack:U0CAR0L"
CHANNEL = "slack:C0BQUH14FK3"


def _linked(login: str, name: str) -> CollaboratorIdentity:
    """A person whose ``users`` row the run resolved, keyed on their GitHub login."""
    return CollaboratorIdentity(
        display_name=name,
        commit_name=name,
        commit_email=f"{login}@users.noreply.github.com",
        github_login=login,
    )


P_ALICE = ThreadParticipant(
    identity=_linked("alice", "Alice"),
    person_id=ALICE,
    workspace_admin=True,
    email="alice@example.com",
    linked=True,
)
P_BOB = ThreadParticipant(
    identity=_linked("bob", "Bob"), person_id=BOB, draft_prs=False, linked=True
)
P_BOB_INSTRUCTED = ThreadParticipant(
    identity=_linked("bob", "Bob"),
    person_id=BOB,
    draft_prs=False,
    linked=True,
    instructions="Never use ripgrep.\nRun `make lint` before every push.",
)
# Nobody linked Carol, so Slack's display name is all anyone knows about her.
P_CAROL = ThreadParticipant(
    identity=CollaboratorIdentity(display_name="Carol", commit_name="", commit_email=""),
    person_id=CAROL,
)


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
        text: str,
        ts: str,
        surface: str,
        participants: list[ThreadParticipant],
    ) -> dict[str, Any]:
        self.dump.append(f"## {title}\nGiven: {given}\nWhen: {when}\nThen: {then}\n")
        self.dump.append("### dispatch appends")
        if CHANNEL not in self.introduced:
            block = channel_introduction(
                {"id": CHANNEL, "platform": "slack", "name": "open-swe-dev"}
            )
            self.introduced.add(CHANNEL)
            self._append(cast(str, block["content"]))
        context: dict[str, Any] = {"sender_id": person_id, "surface": surface, "kind": "human"}
        if surface == "slack":
            context["channel_id"] = CHANNEL
            context["data"] = {"timestamp": ts}
        envelope = cast(str, human_input(text, context)["content"])
        self._append(envelope)

        self.dump.append("### run appends")
        person_msgs = PrepareAgentRunMiddleware._participants_messages(
            cast(Any, self.state), participants
        )
        if not person_msgs:
            self.dump.append("(everyone here is already described; nothing re-sent)\n")
        for message in person_msgs:
            self._append(cast(str, message["content"]))
        return {
            "envelope": envelope,
            "people": [cast(str, m["content"]) for m in person_msgs],
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
        then="the channel is introduced, then her envelope, then the one block that describes her",
        person_id=ALICE,
        text="add a greet() helper",
        ts="1789991539.477079",
        surface="slack",
        participants=[P_ALICE],
    )
    assert len(t1["people"]) == 1 and f'id="{ALICE}"' in t1["people"][0]
    assert "commit_email: alice@users.noreply.github.com" in t1["people"][0]
    assert "open_swe_account: linked" in t1["people"][0]
    assert 'timestamp="1789991539.477079"' in t1["envelope"]
    assert f'sender="{ALICE}"' in t1["envelope"]
    turns.append(t1)

    t2 = thread.turn(
        title="Turn 2: Alice follows up",
        given="turn 1 has run to completion",
        when="Alice replies in the same Slack thread: also add a docstring",
        then="only her envelope; nothing about her has changed",
        person_id=ALICE,
        text="also add a docstring",
        ts="1789992088.489369",
        surface="slack",
        participants=[P_ALICE],
    )
    assert t2["people"] == []
    assert f'sender="{ALICE}"' in t2["envelope"]
    turns.append(t2)

    t3 = thread.turn(
        title="Turn 3: Bob joins",
        given="Bob has a linked account and prefers PRs opened ready for review",
        when="Bob replies: make it return bytes",
        then="his envelope and his block; Alice's is not repeated",
        person_id=BOB,
        text="make it return bytes",
        ts="1789992258.131819",
        surface="slack",
        participants=[P_ALICE, P_BOB],
    )
    assert len(t3["people"]) == 1 and f'id="{BOB}"' in t3["people"][0]
    assert "new_prs: ready for review" in t3["people"][0]
    turns.append(t3)

    t4 = thread.turn(
        title="Turn 4: Alice switches to the web dashboard",
        given="the thread has Alice and Bob",
        when="Alice types in the dashboard: ship it",
        then="the same user: id on a web envelope, and nothing else — what she is does not "
        "depend on where she typed",
        person_id=ALICE,
        text="ship it",
        ts="",
        surface="web",
        participants=[P_ALICE, P_BOB],
    )
    assert t4["people"] == []
    assert f'sender="{ALICE}"' in t4["envelope"] and 'surface="web"' in t4["envelope"]
    turns.append(t4)

    t5 = thread.turn(
        title="Turn 5: Bob sets standing instructions, then asks for the PR",
        given="Bob saved personal instructions between turns",
        when="Bob replies: open the PR",
        then="only Bob's block is re-sent, now with his instructions",
        person_id=BOB,
        text="open the PR",
        ts="1789992400.000001",
        surface="slack",
        participants=[P_ALICE, P_BOB_INSTRUCTED],
    )
    assert len(t5["people"]) == 1 and f'id="{BOB}"' in t5["people"][0]
    assert "Never use ripgrep." in t5["people"][0]
    turns.append(t5)

    t6 = thread.turn(
        title="Turn 6: Carol, with no Open SWE account, chimes in",
        given="Carol never signed in to Open SWE",
        when="she replies: can it handle unicode?",
        then="keyed by her Slack id and marked unlinked; with no GitHub login she has no "
        "commit identity to author as",
        person_id=CAROL,
        text="can it handle unicode?",
        ts="1789992500.000001",
        surface="slack",
        participants=[P_ALICE, P_BOB_INSTRUCTED, P_CAROL],
    )
    assert len(t6["people"]) == 1 and f'id="{CAROL}"' in t6["people"][0]
    assert "open_swe_account: unlinked" in t6["people"][0]
    assert "commit_" not in t6["people"][0] and "github_login" not in t6["people"][0]
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
