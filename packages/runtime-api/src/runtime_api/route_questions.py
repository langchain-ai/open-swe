"""The questions the route classifier asks Jev about a request, and the route the answers
take.

Both sides route with it: the backend for the runs it answers from their plan
(``openswe/middleware/model_selection.py``) and the MDA run for its own turns
(``mda/rt/routing.py``). Each words the questions from its own copy of the
``model-selection`` prompts. Jev classifies; ``TaskRoute.route_for`` decides, so changing
which work runs on Performance changes the mapping, not the questions.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

from langchain_typesafe import Choice

from runtime_api.catalog import RouteName
from runtime_api.classifier import DEFAULT_JEV, Jev, JevDecision


@dataclass(frozen=True)
class TaskDecision:
    """What Jev said about a request, and the route it takes."""

    # None when Jev gave no answer to route on.
    route: RouteName | None
    # False when an unsure answer decided the route: it holds for this run alone.
    persist: bool
    # Jev's own choice for each question, sure or not; a question it gave none for is left out.
    answers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def of(
        cls, route: RouteName | None, persist: bool, decisions: dict[str, JevDecision]
    ) -> TaskDecision:
        """The decision, with the choices ``decisions`` recorded."""
        answers = {name: d.choice for name, d in decisions.items() if d.choice is not None}
        return cls(route=route, persist=persist, answers=answers)


class TaskRoute:
    """The route a request takes from how open-ended it is: Performance for a request whose
    answer, cause or approach still has to be found, unless it is one of the well-scoped
    kinds of work Balanced does as well; Balanced for the rest.

    A randomized Balanced/Performance experiment found Performance's advantage in
    satisfaction and corrections only on that open-ended investigation, and none, or the
    reverse, on lookups, ops actions, known-location fixes and specified builds. The task
    type is asked only for the trace.
    """

    TASK_TYPES: Final = (
        "design",
        "explain",
        "feature",
        "bugfix",
        "investigation",
        "writing",
        "ops",
        "lookup",
        "pr_maintenance",
        "no_task",
    )
    # A message that asks for nothing yet, which a later one in the thread may.
    NO_TASK: Final = "no_task"
    INVESTIGATE: Final = ("yes", "no")
    CARVE_OUTS: Final = (
        "debug_known",
        "ops_action",
        "lookup_status",
        "specified_design",
        "none",
    )
    # Well-scoped work that stays on Balanced even when its answer is unknown.
    STAY_BALANCED: Final = frozenset({"debug_known", "ops_action", "specified_design"})
    # Each question, the prompt folder that words it, and its choices.
    QUESTIONS: Final = (
        ("task_type", "task-type", TASK_TYPES),
        ("investigate", "investigate", INVESTIGATE),
        ("carve_out", "carve-out", CARVE_OUTS),
    )
    # How much of a long request the classifier reads: its start and its end, so a
    # question that comes before pasted context (a Slack question before its channel
    # history) and an ask that comes after it both reach it.
    EXCERPT_CHARS: Final = 4_000

    @classmethod
    def excerpt(cls, task: str) -> str:
        """The part of ``task`` the classifier reads."""
        if len(task) <= 2 * cls.EXCERPT_CHARS:
            return task
        return f"{task[: cls.EXCERPT_CHARS]}\n…\n{task[-cls.EXCERPT_CHARS :]}"

    @classmethod
    def questions(cls, prompt: Callable[[str], str]) -> dict[str, Choice]:
        """The questions, worded by the prompts ``prompt`` renders."""
        return {
            name: Choice(
                instructions=prompt(f"model-selection/{folder}/instructions"),
                criteria={
                    choice: prompt(f"model-selection/{folder}/{choice}") for choice in choices
                },
            )
            for name, folder, choices in cls.QUESTIONS
        }

    @classmethod
    def route_for(cls, investigate: str | None, carve_out: str | None) -> RouteName | None:
        """The route a classified request takes; None when Jev gave no answer."""
        if investigate is None or carve_out is None:
            return None
        if investigate == "yes" and carve_out not in cls.STAY_BALANCED:
            return "performance"
        return "balanced"

    @classmethod
    async def classify(
        cls,
        task: str,
        *,
        prompt: Callable[[str], str],
        jev: Jev = DEFAULT_JEV,
    ) -> tuple[RouteName | None, bool]:
        """The route ``task`` takes, and whether the thread keeps it: a route an unsure
        answer decided holds for this run alone."""
        decision = await cls.decide(task, prompt=prompt, jev=jev)
        return decision.route, decision.persist

    @classmethod
    async def decide(
        cls,
        task: str,
        *,
        prompt: Callable[[str], str],
        jev: Jev = DEFAULT_JEV,
    ) -> TaskDecision:
        """The route ``task`` takes, with the answers that decided it.

        Jev's choices route even when it is unsure, as they did in the experiment; an
        unsure answer routes this run alone, so a later turn classifies again.
        """
        decisions = {name: JevDecision() for name, _, _ in cls.QUESTIONS}
        await jev.choose(cls.excerpt(task), questions=cls.questions(prompt), decisions=decisions)
        investigate, carve_out = decisions["investigate"], decisions["carve_out"]
        route = cls.route_for(investigate.choice, carve_out.choice)
        sure = investigate.outcome == "accepted" and carve_out.outcome == "accepted"
        return TaskDecision.of(route, route is not None and sure, decisions)
