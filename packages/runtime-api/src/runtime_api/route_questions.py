"""The two questions the route classifier asks Jev about a request, and the route the
answers take.

Both sides route with it: the backend for the runs it answers from their plan
(``openswe/middleware/model_selection.py``) and the MDA run for its own turns
(``mda/rt/routing.py``). Each words the questions from its own copy of the
``model-selection`` prompts. Jev classifies; ``TaskRoute.route_for`` decides, so changing
which work runs on Performance changes the mapping, not the questions.
"""

from collections.abc import Callable
from typing import Final

import httpx2
from langchain_typesafe import Choice

from runtime_api.catalog import RouteName
from runtime_api.classifier import JevDecision, select_jev_choices


class TaskRoute:
    """The route a request takes, from what kind of task it is and how open-ended."""

    TASK_TYPES: Final = (
        "design",
        "feature",
        "investigation",
        "bugfix",
        "explain",
        "lookup",
        "pr_maintenance",
        "ops_docs_refactor",
        "other",
    )
    COMPLEXITIES: Final = ("routine", "involved", "open_ended")
    # Design (judgment calls included) and questions that need understanding code always
    # run on Performance; building or fixing does only when the approach is still open.
    PERFORMANCE_TASKS: Final = frozenset({"design", "explain"})
    OPEN_ENDED_PERFORMANCE_TASKS: Final = frozenset({"feature", "bugfix"})

    @classmethod
    def questions(cls, prompt: Callable[[str], str]) -> dict[str, Choice]:
        """The two questions, worded by the prompts ``prompt`` renders."""
        return {
            "task_type": Choice(
                instructions=prompt("model-selection/task-type/instructions"),
                criteria={
                    name: prompt(f"model-selection/task-type/{name}") for name in cls.TASK_TYPES
                },
            ),
            "complexity": Choice(
                instructions=prompt("model-selection/complexity/instructions"),
                criteria={
                    name: prompt(f"model-selection/complexity/{name}") for name in cls.COMPLEXITIES
                },
            ),
        }

    @classmethod
    def route_for(cls, task_type: str | None, complexity: str | None) -> RouteName | None:
        """The route a classified request takes; None when the task type is unknown."""
        if task_type is None:
            return None
        if task_type in cls.PERFORMANCE_TASKS:
            return "performance"
        if task_type in cls.OPEN_ENDED_PERFORMANCE_TASKS and complexity == "open_ended":
            return "performance"
        return "balanced"

    @classmethod
    async def classify(
        cls,
        task: str,
        *,
        prompt: Callable[[str], str],
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> tuple[RouteName | None, bool]:
        """The route ``task`` takes, and whether the thread keeps it: a route an unsure
        answer decided holds for this run alone."""
        decisions = {"task_type": JevDecision(), "complexity": JevDecision()}
        answers = await select_jev_choices(
            task, questions=cls.questions(prompt), decisions=decisions, transport=transport
        )
        route = cls.route_for(answers["task_type"], answers["complexity"])
        sure = decisions["task_type"].outcome == "accepted" and (
            answers["task_type"] not in cls.OPEN_ENDED_PERFORMANCE_TASKS
            or decisions["complexity"].outcome == "accepted"
        )
        return route, route is not None and sure
