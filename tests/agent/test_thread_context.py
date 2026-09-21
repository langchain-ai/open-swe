"""How a thread looks to the model as people come and go — executable spec.

Each turn renders through the real dispatch and prepare-run code, its "Then" is
asserted, and the rendered walkthrough must match ``thread_context.md`` next to
this file. Regenerate that fixture with ``UPDATE_THREAD_CONTEXT_FIXTURE=1``.
"""

import os
from pathlib import Path
from typing import Any, cast

from langchain_core.messages import HumanMessage

from agent.input_messages import (
    COLLABORATION_SENDER_ID,
    SENDER_CONTEXT_SENDER_ID,
    PersonIdentity,
    channel_introduction,
    human_input,
    person_introduction,
)
from agent.prompt import construct_collaboration_context, construct_sender_context
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

ATTRIBUTION_ELISION = "  …Collaborative Attribution rules, unchanged from Turn 1…"


class Thread:
    """One thread accumulating state the way dispatch and the run append to it."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {"messages": []}
        self.introduced: set[str] = set()
        self.roster_emitted = False
        self.doc: list[str] = []

    def _append(self, content: str) -> None:
        self.state["messages"].append(HumanMessage(content=content))

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
        self.doc.append(
            f"\n## {title}\n\n**Given** {given}  \n**When** {when}  \n**Then** {then}\n"
        )
        dispatched: list[tuple[str, str]] = []
        if CHANNEL not in self.introduced:
            block = channel_introduction(
                {"id": CHANNEL, "platform": "slack", "name": "open-swe-dev"}
            )
            self.introduced.add(CHANNEL)
            dispatched.append(("channel introduced once per thread", cast(str, block["content"])))
        if person is not None:
            content = cast(str, person_introduction(person)["content"])
            if content not in self.introduced:
                self.introduced.add(content)
                dispatched.append(("person introduced (dedupes on content hash)", content))
        context: dict[str, Any] = {"sender_id": person_id, "surface": surface, "kind": "human"}
        if surface == "slack":
            context["channel_id"] = CHANNEL
            context["data"] = {"timestamp": ts}
        envelope = cast(str, human_input(text, context)["content"])
        dispatched.append(("the message; `sender` is the key the pointer uses", envelope))
        for _, content in dispatched:
            self._append(content)

        roster_msgs = PrepareAgentRunMiddleware._collaboration_messages(
            cast(Any, self.state), construct_collaboration_context(participants)
        )
        pointer_msgs = PrepareAgentRunMiddleware._sender_context_messages(
            cast(Any, self.state),
            construct_sender_context(display_name, person_id),
            sender_id=person_id,
        )
        for message in [*roster_msgs, *pointer_msgs]:
            self._append(cast(str, message["content"]))

        roster = cast(str, roster_msgs[-1]["content"]) if roster_msgs else None
        pointer = cast(str, pointer_msgs[-1]["content"])
        self.doc.append("**Dispatch appends:**\n")
        for label, content in dispatched:
            self._show(label, content)
        self.doc.append("**The run then appends:**\n")
        if roster is None:
            self.doc.append(
                f"<sub>`{COLLABORATION_SENDER_ID}`</sub> *(unchanged — not repeated)*\n"
            )
        else:
            label = (
                f"`{COLLABORATION_SENDER_ID}` — roster (re-emitted only because it changed)"
                if self.roster_emitted
                else f"`{COLLABORATION_SENDER_ID}` — roster, once per thread"
            )
            self._show(label, roster, elide_attribution=self.roster_emitted)
            self.roster_emitted = True
        self._show(f"`{SENDER_CONTEXT_SENDER_ID}` — the per-turn pointer", pointer)
        return {
            "dispatched": dispatched,
            "roster": roster,
            "pointer": pointer,
            "envelope": envelope,
        }

    def _show(self, label: str, text: str, *, elide_attribution: bool = False) -> None:
        if elide_attribution and "### Collaborative Attribution" in text:
            head = text[: text.index("### Collaborative Attribution")].rstrip()
            text = f"{head}\n\n{ATTRIBUTION_ELISION}"
        self.doc.append(f"<sub>{label} · ~{len(text) // 4} tokens</sub>\n\n```xml\n{text}\n```\n")


def walkthrough() -> tuple[str, list[dict[str, Any]]]:
    thread = Thread()
    thread.doc.append(
        """# How a thread looks to the model

Rendered by `tests/agent/test_thread_context.py` through the real renderers
(`agent.input_messages`, `agent.prompt`, `agent.server.PrepareAgentRunMiddleware`);
the test fails if this file and the code disagree. Every block is the exact text
the model receives; only the Collaborative Attribution boilerplate is elided
after its first appearance.

Two layers append to the thread. **Dispatch** (Slack webhook / dashboard) adds a
person's `<dynamic-context>` introduction the first time they appear, then their
message as an `<input-message>` envelope whose `sender=` is their canonical
`user:<uuid>`. **The run** then adds the thread-level `system:collaboration`
roster — every participant's commit identity, permissions and standing
instructions — only when it has changed, and a one-line `system:sender-context`
pointer every turn.
"""
    )
    turns: list[dict[str, Any]] = []

    t1 = thread.turn(
        title="Turn 1 — Alice starts the thread from Slack",
        given="an empty thread; Alice has linked her GitHub account and is a workspace admin",
        when="she mentions the bot: *add a greet() helper*",
        then="the channel and Alice are introduced, her message lands, the roster is emitted for the first time, and the pointer names her",
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
    assert t1["roster"] is not None and "**Alice**" in t1["roster"]
    assert "Sent by **Alice**" in t1["pointer"]
    turns.append(t1)

    t2 = thread.turn(
        title="Turn 2 — Alice follows up",
        given="Turn 1 has run to completion",
        when="Alice replies in the same Slack thread: *also add a docstring*",
        then="only her envelope and the pointer are added — nobody new, nothing about her has changed, so the roster is not repeated",
        person_id=ALICE,
        person=None,
        text="also add a docstring",
        ts="1789992088.489369",
        surface="slack",
        display_name="Alice",
        participants=[P_ALICE],
    )
    assert t2["roster"] is None
    assert "Sent by **Alice**" in t2["pointer"]
    turns.append(t2)

    t3 = thread.turn(
        title="Turn 3 — Bob joins",
        given="Alice's two turns; Bob has a linked account and prefers PRs opened ready for review",
        when="Bob replies in the thread: *make it return bytes*",
        then="Bob is introduced, the roster is re-emitted because it gained a participant, and the pointer names Bob",
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
    assert (
        t3["roster"] is not None
        and "**Bob**" in t3["roster"]
        and "ready for review" in t3["roster"]
    )
    turns.append(t3)

    t4 = thread.turn(
        title="Turn 4 — Alice switches to the web dashboard",
        given="the thread now has Alice and Bob",
        when="Alice opens the thread in the dashboard and types *ship it*",
        then="her envelope carries the same `user:` id as her Slack messages — one person, two surfaces — so the pointer resolves to the same roster entry and the roster is not repeated; her introduction reappears once because the dashboard knows different attributes about her",
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
    assert t4["roster"] is None
    assert f'sender="{ALICE}"' in t4["envelope"] and 'surface="web"' in t4["envelope"]
    turns.append(t4)

    t5 = thread.turn(
        title="Turn 5 — Bob sets standing instructions, then asks for the PR",
        given="Bob saved personal instructions in the dashboard between turns",
        when="Bob replies: *open the PR*",
        then="the roster is re-emitted because Bob's entry changed, carrying his instructions; the pointer names Bob",
        person_id=BOB,
        person=None,
        text="open the PR",
        ts="1789992400.000001",
        surface="slack",
        display_name="Bob",
        participants=[P_ALICE, P_BOB_INSTRUCTED],
    )
    assert t5["roster"] is not None and "Never use ripgrep." in t5["roster"]
    turns.append(t5)

    t6 = thread.turn(
        title="Turn 6 — Carol, who has no Open SWE account, chimes in",
        given="Carol is in the Slack channel but never signed in to Open SWE",
        when="she replies: *can it handle unicode?*",
        then="she is keyed by her Slack id, marked `unlinked`, and gets a roster entry with only what Slack knows about her",
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
    assert t6["roster"] is not None and f"`{CAROL}`" in t6["roster"]
    assert any("<open_swe_account>unlinked</open_swe_account>" in c for _, c in t6["dispatched"])
    turns.append(t6)

    thread.doc.append(
        """
## What this buys

- **Per turn cost** is the envelope plus a ~40-token pointer. Everything about a person is stated once.
- **The roster re-emits only on change** — a new participant, or someone's settings or standing instructions changing. Nothing run-scoped lives in it: the PR footer, which names the model, is stamped by `open_pull_request` itself.
- **One person, one id.** Alice's Slack and dashboard messages share `user:…`, so the model never has to guess that two senders are the same human. Carol, who never signed in, keeps her surface id and is visibly `unlinked`.
- **Nothing expires.** The failure this replaces was a sender block emitted once and described as "this turn only"; three turns later the agent concluded it had no identity and refused to work.
"""
    )
    return "\n".join(thread.doc), turns


def test_thread_context_matches_fixture() -> None:
    rendered, turns = walkthrough()
    assert len(turns) == 6
    if os.environ.get("UPDATE_THREAD_CONTEXT_FIXTURE"):
        FIXTURE.write_text(rendered)
    assert rendered == FIXTURE.read_text(), (
        "thread_context.md is out of date; rerun with UPDATE_THREAD_CONTEXT_FIXTURE=1"
    )
