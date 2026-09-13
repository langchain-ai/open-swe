"""Evaluators for the routing eval.

Route correctness is scored deterministically against the dataset label. Title
quality is scored by an LLM judge against the same rules the agent is given.
Exploration cost is recorded as feedback so runs can be compared on price, not
just accuracy.
"""

import os
from typing import Any

from langchain_openai import ChatOpenAI
from langsmith.schemas import Example, Run
from pydantic import BaseModel, ConfigDict, Field

from evals.routing.target import RoutingOutcome

JUDGE_MODEL = os.environ.get("ROUTING_EVAL_JUDGE_MODEL", "gpt-5.6-sol")

_TIERS = ("fast", "balanced", "performance")

_TITLE_JUDGE_PROMPT = """You are grading a thread title chosen by a coding agent.

The user's request:
{prompt}

The title:
{title}

Rules the title must follow:
- 3 to 8 words, sentence case (capitalize only the first word and proper nouns or acronyms).
- Names the durable subject and desired outcome, not the current workflow step.
- Does not claim the work is complete.
- No PR numbers, quotes, labels, filler, trailing punctuation, or the project's own name.

Respond with ONLY a JSON object:
{{"score": 0.0-1.0, "reasoning": "one sentence", "violations": ["rule", ...]}}"""


class TitleVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore")
    score: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""
    violations: list[str] = Field(default_factory=list)


_judge: Any = None


def _get_judge() -> Any:
    global _judge
    if _judge is None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("Set OPENAI_API_KEY for the judge.")
        _judge = ChatOpenAI(model=JUDGE_MODEL, max_retries=3).with_structured_output(
            TitleVerdict, method="json_schema"
        )
    return _judge


def _outcome(run: Run) -> RoutingOutcome | None:
    if not isinstance(run.outputs, dict):
        return None
    return RoutingOutcome.model_validate(run.outputs)


def _accepted(example: Example) -> list[str]:
    """Every route the label allows. A boundary task accepts both sides."""
    accepted = (example.outputs or {}).get("accepted")
    if isinstance(accepted, list):
        routes = [route for route in accepted if isinstance(route, str)]
        if routes:
            return routes
    return ["balanced"]


def _tier_bounds(accepted: list[str]) -> tuple[int, int] | None:
    """Cheapest and most expensive accepted tier, ignoring the non-tier ``none``."""
    indexes = [_TIERS.index(route) for route in accepted if route in _TIERS]
    return (min(indexes), max(indexes)) if indexes else None


def _actual(outcome: RoutingOutcome) -> str:
    if outcome.exited and outcome.route:
        return outcome.route
    if outcome.finished_without_exit:
        return "none"
    return "timeout"


def route_match(run: Run, example: Example) -> dict[str, Any]:
    outcome = _outcome(run)
    if outcome is None:
        return {"key": "route_match", "score": 0, "comment": "no outputs"}
    accepted, actual = _accepted(example), _actual(outcome)
    return {
        "key": "route_match",
        "score": int(actual in accepted),
        "comment": f"accepted={'|'.join(accepted)} actual={actual}",
    }


def route_distance(run: Run, example: Example) -> dict[str, Any]:
    """Tiers outside the accepted band; 0 when inside it. A timeout counts as 3."""
    outcome = _outcome(run)
    accepted = _accepted(example)
    actual = _actual(outcome) if outcome else "timeout"
    if actual in accepted:
        distance = 0
    elif actual in _TIERS and (bounds := _tier_bounds(accepted)) is not None:
        low, high = bounds
        index = _TIERS.index(actual)
        distance = low - index if index < low else index - high
    else:
        distance = 3
    return {
        "key": "route_distance",
        "score": distance,
        "comment": f"{actual} vs {'|'.join(accepted)}",
    }


def over_routed(run: Run, example: Example) -> dict[str, Any]:
    """1 when the agent chose a tier above everything the label accepts."""
    outcome = _outcome(run)
    accepted = _accepted(example)
    actual = _actual(outcome) if outcome else "timeout"
    if actual in accepted:
        return {"key": "over_routed", "score": 0}
    bounds = _tier_bounds(accepted)
    if actual in _TIERS and bounds is not None:
        return {"key": "over_routed", "score": int(_TIERS.index(actual) > bounds[1])}
    # A routed tier where only "none" is accepted is spending money on nothing.
    return {"key": "over_routed", "score": int(actual in _TIERS and bounds is None)}


def under_routed(run: Run, example: Example) -> dict[str, Any]:
    """1 when the agent chose below the accepted band, or never routed real work."""
    outcome = _outcome(run)
    accepted = _accepted(example)
    actual = _actual(outcome) if outcome else "timeout"
    if actual in accepted:
        return {"key": "under_routed", "score": 0}
    bounds = _tier_bounds(accepted)
    if actual in _TIERS and bounds is not None:
        return {"key": "under_routed", "score": int(_TIERS.index(actual) < bounds[0])}
    return {"key": "under_routed", "score": int(actual == "none" and bounds is not None)}


def exploration_cost(run: Run, example: Example) -> dict[str, Any]:
    del example
    outcome = _outcome(run)
    if outcome is None:
        return {"results": []}
    return {
        "results": [
            {"key": "model_calls_before_exit", "score": outcome.model_calls_before_exit},
            {"key": "tool_calls_before_exit", "score": outcome.tool_calls_before_exit},
            {"key": "rejected_tool_calls", "score": outcome.rejected_tool_calls},
            # Thousands, because LangSmith rejects a feedback score above 99999.9999.
            {"key": "pre_exit_input_ktokens", "score": round(outcome.input_tokens / 1000, 2)},
            {"key": "pre_exit_output_ktokens", "score": round(outcome.output_tokens / 1000, 2)},
            {"key": "seconds_to_decision", "score": outcome.seconds_to_decision},
            {"key": "timed_out", "score": int(outcome.timed_out)},
        ]
    }


def title_format(run: Run, example: Example) -> dict[str, Any]:
    """Deterministic half of the title check: length and punctuation."""
    del example
    outcome = _outcome(run)
    if outcome is None or not outcome.title:
        return {"key": "title_format", "score": None, "comment": "no title"}
    title = outcome.title.strip()
    words = title.split()
    problems = []
    if not 3 <= len(words) <= 8:
        problems.append(f"{len(words)} words")
    if len(title) > 80:
        problems.append("over 80 chars")
    if title[-1:] in ".!?:;":
        problems.append("trailing punctuation")
    if title.startswith(("<", "`", '"', "'")):
        problems.append("markup or quotes")
    return {"key": "title_format", "score": int(not problems), "comment": ", ".join(problems)}


async def title_quality(run: Run, example: Example) -> dict[str, Any]:
    outcome = _outcome(run)
    if outcome is None or not outcome.title:
        return {"key": "title_quality", "score": None, "comment": "no title"}
    prompt = str((example.inputs or {}).get("prompt", ""))
    verdict = await _get_judge().ainvoke(
        _TITLE_JUDGE_PROMPT.format(prompt=prompt, title=outcome.title)
    )
    if not isinstance(verdict, TitleVerdict):
        return {"key": "title_quality", "score": None, "comment": "judge returned no verdict"}
    comment = verdict.reasoning
    if verdict.violations:
        comment += " | " + ", ".join(verdict.violations)
    return {"key": "title_quality", "score": verdict.score, "comment": comment}


def aggregate(runs: list[Run], examples: list[Example]) -> dict[str, Any]:
    outcomes = [(_outcome(run), example) for run, example in zip(runs, examples, strict=True)]
    scored = [(o, e) for o, e in outcomes if o is not None]
    if not scored:
        return {"results": []}
    matches = sum(_actual(o) in _accepted(e) for o, e in scored)
    confusion: dict[str, int] = {}
    for o, e in scored:
        key = f"{'|'.join(_accepted(e))}->{_actual(o)}"
        confusion[key] = confusion.get(key, 0) + 1
    routed = [o for o, _ in scored if o.exited]
    mean = lambda values: round(sum(values) / len(values), 2) if values else 0.0  # noqa: E731
    return {
        "results": [
            {"key": "route_accuracy", "score": round(matches / len(scored), 3)},
            {"key": "timeouts", "score": sum(o.timed_out for o, _ in scored)},
            {
                "key": "mean_tool_calls_before_exit",
                "score": mean([o.tool_calls_before_exit for o in routed]),
            },
            {
                "key": "mean_pre_exit_input_ktokens",
                "score": mean([o.input_tokens / 1000 for o in routed]),
            },
            {
                "key": "mean_seconds_to_decision",
                "score": mean([o.seconds_to_decision for o in routed]),
            },
            {
                "key": "total_rejected_tool_calls",
                "score": sum(o.rejected_tool_calls for o in routed),
            },
            {
                "key": "confusion",
                "score": None,
                "comment": ", ".join(f"{k}: {v}" for k, v in sorted(confusion.items())),
            },
        ]
    }
