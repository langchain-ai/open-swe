"""Random teams, code owners, and habits, each played for a week against every rule.

A failure shrinks to the smallest team that breaks a rule. Its page goes to
``logs/behavior/human_review/counterexample.md``, and the failure message is the scenario as
Python, ready to paste into ``test_review_scenarios.py``.

``SCENARIO_EXAMPLES`` sets how many teams to try. ``SCENARIO_IGNORE`` skips known findings
(comma-separated substrings) so the search can look past them.
"""

import asyncio
import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import get_args

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.human_review.scenario import (
    BANGALORE,
    LONDON,
    NEW_YORK,
    SAN_FRANCISCO,
    TOKYO,
    Habit,
    ReviewScenario,
)

_ZONES = (NEW_YORK, SAN_FRANCISCO, LONDON, BANGALORE, TOKYO)
_ZONE_NAMES = {
    NEW_YORK: "NEW_YORK",
    SAN_FRANCISCO: "SAN_FRANCISCO",
    LONDON: "LONDON",
    BANGALORE: "BANGALORE",
    TOKYO: "TOKYO",
}
_NAMES = ("ada", "bob", "carol", "dana", "erin", "frank", "grace")
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_IGNORED = tuple(part for part in os.environ.get("SCENARIO_IGNORE", "").split(",") if part)
_COUNTEREXAMPLE = (
    Path(__file__).parents[2] / "logs" / "behavior" / "human_review" / "counterexample.md"
)


_EVERY_AREA = "Every code owner area needs an approval from one of its own owners."


@dataclass(frozen=True)
class Team:
    """One generated scenario and the week it plays."""

    zones: tuple[tuple[str, str], ...]
    areas: tuple[tuple[str, ...], ...]
    files_per_area: tuple[int, ...]
    habits: tuple[tuple[str, Habit, int], ...]
    assignment_minutes: int
    start_day: int
    start_hour: int
    wait_hours: int
    volunteer: str | None
    approver: tuple[str, int] | None
    every_area_reviewed: bool = False

    @property
    def author(self) -> str:
        return self.zones[0][0]

    @property
    def files(self) -> list[str]:
        return [
            f"area{area}/file{n}.py"
            for area, count in enumerate(self.files_per_area)
            for n in range(count)
        ]

    def scenario(self) -> ReviewScenario:
        scenario = ReviewScenario(assignment_minutes=self.assignment_minutes)
        people = {login: scenario.person(login, zone) for login, zone in self.zones}
        for area, owners in enumerate(self.areas):
            scenario.owns(f"/area{area}/", *(people[o] for o in owners))
        for login, action, hours in self.habits:
            scenario.habit(people[login], action, after=_hours(hours))
        if self.every_area_reviewed:
            scenario.reviewer_instructions(_EVERY_AREA)
        scenario.pull_request(author=people[self.author], files=self.files)
        return scenario

    async def play(self, scenario: ReviewScenario) -> None:
        author = scenario.people[self.author]
        async with scenario.from_(author.at(self.start), check_rules=False):
            with scenario.step("Review requested"):
                await scenario.request_review()
                await scenario.wait(hours=self.wait_hours)
            if self.volunteer and scenario.card_offers_signup:
                with scenario.step(f"{self.volunteer} volunteers"):
                    await scenario.clicks(scenario.people[self.volunteer], "ill_review")
            if self.approver:
                login, hours = self.approver
                with scenario.step(f"{login} approves on GitHub unasked"):
                    await scenario.wait(hours=hours)
                    await scenario.reviews_on_github(scenario.people[login])
            with scenario.step("A week passes"):
                await scenario.wait(days=7)

    @property
    def start(self) -> str:
        return f"{_DAYS[self.start_day]} {self.start_hour:02d}:00"

    def to_python(self) -> str:
        """This team as a scenario test to paste into ``test_review_scenarios.py``."""
        lines = [
            "async def test_found_by_the_invariant_search(scenario: ReviewScenario) -> None:",
            '    """Found by the invariant search.',
            "",
            "    Describe what should happen here.",
            '    """',
        ]
        if self.assignment_minutes != 120:
            lines.append(f"    scenario.assignment_minutes = {self.assignment_minutes}")
        lines += [
            f'    {login} = scenario.person("{login}", {_ZONE_NAMES[zone]})'
            for login, zone in self.zones
        ]
        lines += [
            f'    scenario.owns("/area{i}/", {", ".join(owners)})'
            for i, owners in enumerate(self.areas)
        ]
        lines += [
            f'    scenario.habit({login}, "{action}", after=timedelta(hours={hours}))'
            for login, action, hours in self.habits
            if action != "ignore"
        ]
        if self.every_area_reviewed:
            lines.append(f"    scenario.reviewer_instructions({_EVERY_AREA!r})")
        lines += [
            f"    scenario.pull_request(author={self.author}, files={self.files!r})",
            "",
            f'    async with scenario.from_({self.author}.at("{self.start}")):',
            '        with scenario.step("Review requested"):',
            "            await scenario.request_review()",
            f"            await scenario.wait(hours={self.wait_hours})",
        ]
        if self.volunteer:
            lines += [
                f'        with scenario.step("{self.volunteer} volunteers"):',
                f'            await scenario.clicks({self.volunteer}, "ill_review")',
            ]
        if self.approver:
            login, hours = self.approver
            lines += [
                f'        with scenario.step("{login} approves on GitHub unasked"):',
                f"            await scenario.wait(hours={hours})",
                f"            await scenario.reviews_on_github({login})",
            ]
        lines += [
            '        with scenario.step("A week passes"):',
            "            await scenario.wait(days=7)",
        ]
        return "\n".join(lines)


def _hours(hours: int) -> timedelta:
    return timedelta(hours=hours)


@st.composite
def teams(draw: st.DrawFn) -> Team:
    names = _NAMES[: draw(st.integers(3, len(_NAMES)))]
    author, others = names[0], names[1:]
    areas: list[tuple[str, ...]] = []
    for _ in range(draw(st.integers(1, 3))):
        owners = draw(st.lists(st.sampled_from(others), min_size=1, max_size=3, unique=True))
        if draw(st.booleans()):
            owners.append(author)
        areas.append(tuple(owners))
    return Team(
        zones=tuple((name, draw(st.sampled_from(_ZONES))) for name in names),
        areas=tuple(areas),
        files_per_area=tuple(draw(st.integers(1, 3)) for _ in areas),
        habits=tuple(
            (name, draw(st.sampled_from(get_args(Habit.__value__))), draw(st.integers(1, 6)))
            for name in others
        ),
        assignment_minutes=draw(st.sampled_from((60, 120))),
        start_day=draw(st.integers(0, 6)),
        start_hour=draw(st.integers(0, 23)),
        wait_hours=draw(st.integers(0, 4)),
        volunteer=draw(st.none() | st.sampled_from(others)),
        approver=draw(st.none() | st.tuples(st.sampled_from(others), st.integers(1, 30))),
        every_area_reviewed=draw(st.booleans()),
    )


@settings(
    max_examples=int(os.environ.get("SCENARIO_EXAMPLES", "150")),
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
    # CI replays the same teams every run; a local run explores new ones.
    derandomize=bool(os.environ.get("CI")),
)
@given(teams())
def test_every_team_keeps_every_rule(team: Team) -> None:
    scenario = team.scenario()
    asyncio.run(team.play(scenario))
    broken = scenario.violations(ignoring=_IGNORED)
    if broken:
        _COUNTEREXAMPLE.parent.mkdir(parents=True, exist_ok=True)
        _COUNTEREXAMPLE.write_text(
            scenario.render(title="Found by the invariant search", summary="\n".join(broken))
        )
    assert not broken, "\n".join([*broken, "", team.to_python()])
