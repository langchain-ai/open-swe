"""Scenarios: executable, rendered specifications of behavior that unfolds over time.

A scenario plays the real code against a faked outside world while time travels. It is
written as typed Python and reads as a story:

    async def test_two_unaccepted_picks_escalate_to_the_author(office: ReviewOffice) -> None:
        \"\"\"Two unaccepted picks escalate to the author.

        Nobody signs up for Ada's pull request. ...
        \"\"\"
        ...
        async with office.from_(ada.at("Mon 10:00")):
            with office.step("Ada asks for a review; nobody signs up in time"):
                await office.request_review()
                await office.wait(hours=2)
                office.expect(picked(bob), dm_to(bob, "reviewer_pick"))

The core here knows nothing about any one feature. It owns the clock, the scheduler and
store Open SWE schedules work through, the timeline of moments, steps and expectations,
invariants, boundary patching, and rendering. Boundaries (Slack, GitHub) and domain packs
(human review) build on it.
"""

import os
import re
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Literal, Protocol
from unittest.mock import patch
from zoneinfo import ZoneInfo

import time_machine
from pydantic import BaseModel, JsonValue

from openswe.human_review.picking import WorkHours
from openswe.utils import thread_ops

# Scenarios happen in the week of Monday 5 October 2026.
WEEK = datetime(2026, 10, 5)
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_TICK = timedelta(minutes=15)
OPEN_SWE = "openswe"
AGENT = "agent"

type MomentKind = Literal[
    "step", "decision", "message", "edit", "delete", "action", "agent", "note", "deadline"
]


# Actors.


@dataclass(frozen=True)
class Person:
    """Someone in the story, with the Slack time zone their work hours follow."""

    login: str
    zone: ZoneInfo

    @property
    def slack_id(self) -> str:
        return f"U_{self.login}"

    @property
    def place(self) -> str:
        return self.zone.key.split("/")[-1].replace("_", " ")

    def at(self, when: str) -> datetime:
        """``"Mon 10:00"`` in this person's time zone, in the scenario week."""
        day, clock = when.split()
        hour, minute = map(int, clock.split(":"))
        date = (WEEK + timedelta(days=_DAYS.index(day))).date()
        return datetime.combine(date, time(hour, minute), self.zone)

    def on_shift(self, at: datetime) -> bool:
        return WorkHours(self.zone).on_shift(at)

    def local(self, at: datetime) -> str:
        return at.astimezone(self.zone).strftime("%a %H:%M")


# The timeline.


class Moment(BaseModel):
    """One thing that happened. ``source`` and ``target`` are actor names for the diagram."""

    at: datetime
    step: int
    kind: MomentKind
    text: str
    source: str = OPEN_SWE
    target: str = ""
    label: str = ""
    ref: str = ""
    data: dict[str, JsonValue] = {}

    @property
    def seen(self) -> str | None:
        """How ``Seen`` names this moment; ``None`` for what expectations do not check."""
        if self.kind == "decision":
            return self.text
        if self.kind == "message":
            return f"message {self.target}: {self.label or 'message'}"
        if self.kind == "edit":
            return f"edit {self.target}"
        if self.kind == "delete":
            return f"delete {self.target}"
        return None


@dataclass(frozen=True)
class Seen:
    """One thing a step should cause, as ``Scenario.expect`` compares it."""

    line: str

    @classmethod
    def decision(cls, kind: str, *who: Person, cause: str = "") -> Seen:
        line = " ".join(part for part in (kind, ", ".join(p.login for p in who)) if part)
        return cls(f"{line} ({cause})" if cause else line)

    @classmethod
    def message(cls, to: Person, label: str) -> Seen:
        return cls(f"message {to.login}: {label}")

    @classmethod
    def edit(cls, to: Person) -> Seen:
        return cls(f"edit {to.login}")


# The scheduler and store Open SWE schedules work through.


type RunHandler = Callable[[dict[str, JsonValue]], Awaitable[str]]


@dataclass
class _Scheduled:
    due: datetime
    seq: int
    assistant: str
    input: dict[str, JsonValue]


class LangGraph:
    """The LangGraph client: delayed runs become scheduled work, and the store is in memory."""

    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.queue: list[_Scheduled] = []
        self.handlers: dict[str, RunHandler] = {}
        self.items: dict[tuple[tuple[str, ...], str], dict[str, JsonValue]] = {}
        self.on_put: list[Callable[[tuple[str, ...], str, dict[str, JsonValue]], None]] = []
        self.runs = self
        self.store = self

    def handle(self, assistant: str, handler: RunHandler) -> None:
        """Run ``handler`` with a delayed run's input when it comes due."""
        self.handlers[assistant] = handler

    async def create(
        self,
        thread_id: str | None,
        assistant_id: str,
        *,
        input: dict[str, JsonValue],
        after_seconds: int = 0,
        **_: object,
    ) -> dict[str, str]:
        due = self.scenario.now + timedelta(seconds=max(after_seconds, 0))
        self.queue.append(_Scheduled(due, self.scenario.next_seq(), assistant_id, input))
        return {"run_id": f"run-{self.scenario.next_seq()}"}

    async def put_item(
        self, namespace: tuple[str, ...], key: str, value: dict[str, JsonValue], **_: object
    ) -> None:
        self.items[(tuple(namespace), key)] = value
        for listener in self.on_put:
            listener(tuple(namespace), key, value)

    async def get_item(
        self, namespace: tuple[str, ...], key: str, **_: object
    ) -> dict[str, JsonValue] | None:
        value = self.items.get((tuple(namespace), key))
        return None if value is None else {"value": value}

    def due(self) -> _Scheduled | None:
        self.queue.sort(key=lambda item: (item.due, item.seq))
        return self.queue[0] if self.queue and self.queue[0].due <= self.scenario.now else None

    def next_due(self) -> datetime | None:
        return min((item.due for item in self.queue), default=None)


# Boundaries.


type Fake = Callable[..., object]


class Boundary(Protocol):
    """A part of the outside world.

    ``fakes`` replaces public functions wherever the code imported them; ``attribute_fakes``
    replaces one attribute of a class or module, such as a classmethod or a setting.
    """

    def fakes(self) -> list[tuple[object, Fake]]: ...

    def attribute_fakes(self) -> list[tuple[object, str, object]]: ...


def returning[T](value: T) -> Fake:
    async def fake(*_: object, **__: object) -> T:
        return value

    return fake


def instance_method[T](fake: Callable[..., Awaitable[T]]) -> Fake:
    """``fake`` as a replacement for an instance method: it receives the instance first."""

    async def method(instance: object, *args: object, **kwargs: object) -> T:
        return await fake(instance, *args, **kwargs)

    return method


def computing[T](make: Callable[..., T]) -> Fake:
    async def fake(*args: object, **__: object) -> T:
        return make(*args)

    return fake


def patch_everywhere(stack: ExitStack, original: object, fake: object) -> None:
    """Replace ``original`` in every Open SWE module that imported it."""
    for name, module in list(sys.modules.items()):
        if module is None or not (name == "openswe" or name.startswith("openswe.")):
            continue
        for attribute, value in list(vars(module).items()):
            if value is original:
                stack.enter_context(patch.object(module, attribute, fake))


# Invariants.


def invariant[S: "Scenario"](rule: Callable[[S], list[str]]) -> Callable[[S], list[str]]:
    """Mark a method as a rule every play of the scenario must keep."""
    rule.__scenario_invariant__ = True  # type: ignore[attr-defined]
    return rule


# Scenarios.


class Scenario:
    """The clock, the outside world, and everything that happened, for one play."""

    def __init__(self) -> None:
        self.people: dict[str, Person] = {}
        self.timeline: list[Moment] = []
        self.steps: list[str] = []
        self.langgraph = LangGraph(self)
        self._seq = 0
        self._checked = 0
        self._stack = ExitStack()
        self._traveller: time_machine.Coordinates | None = None

    # Setup.

    def person(self, login: str, zone: str) -> Person:
        person = Person(login, ZoneInfo(zone))
        self.people[login] = person
        return person

    def outside_world(self) -> list[Boundary]:
        """The boundaries this scenario fakes; domain packs add their own."""
        return []

    def fakes(self) -> list[tuple[object, Fake]]:
        return [(thread_ops.langgraph_client, lambda: self.langgraph)]

    def attribute_fakes(self) -> list[tuple[object, str, object]]:
        return []

    @asynccontextmanager
    async def from_(self, start: datetime, *, check_rules: bool = True) -> AsyncIterator[None]:
        """Play from ``start``: time travels and the outside world is faked until the block ends.

        A play that finishes without error must also keep every invariant.
        """
        await self.setup()
        self._traveller = self._stack.enter_context(time_machine.travel(start, tick=False))
        for boundary in [self, *self.outside_world()]:
            for original, fake in boundary.fakes():
                patch_everywhere(self._stack, original, fake)
            for target, name, fake in boundary.attribute_fakes():
                self._stack.enter_context(patch.object(target, name, fake))
        try:
            yield
        finally:
            self._stack.close()
        if check_rules and (broken := self.violations()):
            raise AssertionError("broken rules:\n" + "\n".join(f"  - {v}" for v in broken))

    async def setup(self) -> None:
        """Work done once the scenario is described and before time starts."""

    # Time.

    @property
    def now(self) -> datetime:
        return datetime.now(UTC)

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def wait(self, *, days: int = 0, hours: int = 0, minutes: int = 0) -> None:
        """Let time pass, running every scheduled run that comes due and every tick's habits."""
        until = self.now + timedelta(days=days, hours=hours, minutes=minutes)
        while self.now < until:
            due = self.langgraph.next_due() or until
            self._move_to(min(until, max(due, self.now), self.now + _TICK))
            while (scheduled := self.langgraph.due()) is not None:
                self.langgraph.queue.remove(scheduled)
                handler = self.langgraph.handlers[scheduled.assistant]
                status = await handler(scheduled.input)
                self.record("deadline", status)
            await self.tick()

    async def tick(self) -> None:
        """What people do on their own as time passes; domain packs fill it in."""

    def _move_to(self, when: datetime) -> None:
        if self._traveller is None:
            raise AssertionError(
                "the scenario has not started; use `async with scenario.from_(...)`"
            )
        self._traveller.move_to(when)

    # The story.

    def record(self, kind: MomentKind, text: str, **fields: object) -> Moment:
        moment = Moment.model_validate(
            {"at": self.now, "step": len(self.steps), "kind": kind, "text": text, **fields}
        )
        self.timeline.append(moment)
        return moment

    @contextmanager
    def step(self, say: str) -> Iterator[None]:
        """A labelled part of the story; its moments render together under ``say``."""
        self.steps.append(say)
        self._checked = len(self.timeline)
        self.record("step", say)
        yield

    def expect(self, *seen: Seen) -> None:
        """Exactly these decisions, messages, and edits happened since the step began or the last check."""
        actual = [line for moment in self.timeline[self._checked :] if (line := moment.seen)]
        self._checked = len(self.timeline)
        expected = [s.line for s in seen]
        if actual != expected:
            raise AssertionError(
                f"step {len(self.steps)}: {self.steps[-1] if self.steps else ''}\n"
                f"  expected {expected}\n  got      {actual}"
            )

    # Invariants.

    def violations(self, *, ignoring: Sequence[str] = ()) -> list[str]:
        """Every broken rule, from each ``@invariant`` method; ``ignoring`` drops known findings."""
        found: list[str] = []
        for name in dir(type(self)):
            rule = getattr(type(self), name, None)
            if callable(rule) and getattr(rule, "__scenario_invariant__", False):
                found.extend(rule(self))
        return [v for v in dict.fromkeys(found) if not any(part in v for part in ignoring)]

    def rules(self) -> list[str]:
        """The invariants, by their first docstring line, for the rendered page."""
        names: list[str] = []
        for name in sorted(dir(type(self))):
            rule = getattr(type(self), name, None)
            if callable(rule) and getattr(rule, "__scenario_invariant__", False):
                names.append((rule.__doc__ or name).strip().splitlines()[0])
        return names

    # Rendering.

    @property
    def narrator(self) -> Person:
        """Whose clock the notes are in; the first person by default."""
        return next(iter(self.people.values()))

    def render(self, *, title: str, summary: str) -> str:
        return Page(self, title=title, summary=summary).markdown()

    def publish(self, test_name: str, doc: str | None) -> Path:
        """Write the rendered page for the test that played this scenario."""
        title, _, summary = (doc or test_name).strip().partition("\n")
        area = os.environ.get("PYTEST_CURRENT_TEST", "").split("/")[1:2] or ["scenarios"]
        root = Path(
            os.environ.get("SCENARIO_DOCS", Path(__file__).parents[3] / "logs" / "behavior")
        )
        path = root / area[0] / f"{test_name.removeprefix('test_')}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render(title=title.strip().rstrip("."), summary=summary))
        _write_index(root)
        return path


def _write_index(root: Path) -> None:
    """``index.md``: every published scenario by area, with whether its rules held."""
    lines = ["# Behavior", "", "Scenarios that run against the real code, by area.", ""]
    for area in sorted(p for p in root.iterdir() if p.is_dir()):
        lines += [f"## {area.name.replace('_', ' ').capitalize()}", ""]
        for page in sorted(area.glob("*.md")):
            text = page.read_text()
            title = text.splitlines()[0].removeprefix("# ")
            mark = "⚠️" if "**Broken:**" in text else "✅"
            lines.append(f"- {mark} [{title}]({area.name}/{page.name})")
        lines.append("")
    (root / "index.md").write_text("\n".join(lines))


@dataclass
class Page:
    """A scenario as Markdown: the story, a sequence diagram, its rules, and its decisions."""

    scenario: Scenario
    title: str
    summary: str
    lines: list[str] = field(default_factory=list)

    def markdown(self) -> str:
        scenario = self.scenario
        violations = scenario.violations()
        rules = scenario.rules()
        decisions = [m for m in scenario.timeline if m.kind == "decision"]
        deadlines = [m for m in scenario.timeline if m.kind == "deadline"]
        return "\n".join(
            [
                f"# {self.title}",
                "",
                _dedent(self.summary),
                "",
                f"Times in arrows are each person's own; notes are in {scenario.narrator.login}'s "
                f"time ({scenario.narrator.zone.key}).",
                "",
                *self.diagram(),
                "",
                "## Rules this play keeps",
                "",
                *(
                    [f"- ⚠️ **Broken:** {v}" for v in violations]
                    + [f"- {r}" for r in rules if not violations]
                ),
                *([f"- ✅ {r}" for r in rules] if violations else []),
                "",
                "## Decisions in the event log",
                "",
                "| When | Decision |",
                "|---|---|",
                *(f"| {scenario.narrator.local(m.at)} | `{m.text}` |" for m in decisions),
                "",
                "<details><summary>Scheduled runs that came due</summary>",
                "",
                *(f"- {scenario.narrator.local(m.at)}: `{m.text}`" for m in deadlines),
                "",
                "</details>",
                "",
            ]
        )

    def diagram(self) -> list[str]:
        scenario = self.scenario
        narrator = scenario.narrator
        actors = list(scenario.people.values())
        lines = [
            "```mermaid",
            "sequenceDiagram",
            f"    participant {OPEN_SWE} as Open SWE",
            *(f"    participant {p.login} as {p.login} · {p.place}" for p in actors),
        ]
        last = actors[-1].login if actors else OPEN_SWE
        in_step = False
        for moment in scenario.timeline:
            when = narrator.local(moment.at)
            their = (
                scenario.people[moment.target].local(moment.at)
                if moment.target in scenario.people
                else when
            )
            if moment.kind == "step":
                if in_step:
                    lines.append("    end")
                lines += [
                    "    rect rgba(127, 127, 127, 0.08)",
                    f"    Note over {OPEN_SWE},{last}: {when} · {_m(moment.text)}",
                ]
                in_step = True
            elif moment.kind == "decision":
                lines.append(f"    Note right of {OPEN_SWE}: {_m(moment.text)}")
            elif moment.kind == "message":
                label = f"[{moment.label}] " if moment.label else ""
                lines.append(
                    f"    {moment.source}->>{moment.target}: {their} {label}{_m(moment.text, 90)}"
                )
            elif moment.kind == "edit":
                lines.append(
                    f"    {moment.source}-->>{moment.target}: {their} edits: {_m(moment.text, 90)}"
                )
            elif moment.kind == "delete":
                lines.append(f"    {moment.source}--x{moment.target}: {their} deletes a message")
            elif moment.kind == "action":
                actor = scenario.people.get(moment.source)
                local = actor.local(moment.at) if actor else when
                lines.append(f"    {moment.source}->>{OPEN_SWE}: {local} {_m(moment.text)}")
            elif moment.kind == "agent":
                lines.append(f"    Note left of {OPEN_SWE}: agent · {_m(moment.text)}")
            elif moment.kind == "note":
                lines.append(f"    Note over {OPEN_SWE}: {_m(moment.text, 90)}")
        if in_step:
            lines.append("    end")
        lines.append("```")
        return lines


def _m(text: str, limit: int = 140) -> str:
    text = text.replace(";", ",").replace("#", "#35;").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _dedent(text: str) -> str:
    lines = text.strip("\n").splitlines()
    indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    cut = min(indents, default=0)
    return "\n".join(line[cut:] for line in lines).strip()


def slack_plain(text: str) -> str:
    """Slack mrkdwn as readable text: link labels, ``@login`` mentions, no bold markers."""
    text = re.sub(r"<([^|>]+)\|([^>]+)>", r"\2", text)
    return re.sub(r"<@U_([a-z0-9-]+)>", r"@\1", text).replace("*", "")
