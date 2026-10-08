"""Live intent checks: LANGSMITH_TRACING=false python -m evals.model_request.

Uses the existing TypeSafe or LangSmith gateway configuration. An unavailable
classifier is a failed case, including when the expected answer is no request.
"""

import asyncio
from dataclasses import dataclass

from langchain_core.messages import HumanMessage

from openswe.dashboard.options import available_requested_models
from openswe.model_request import ModelRequestIntent, infer_requested_model


@dataclass(frozen=True)
class Case:
    name: str
    request: str
    expected: ModelRequestIntent
    fable_enabled: bool = False


OPUS = ModelRequestIntent(requested_model="anthropic:claude-opus-5-5")
PERFORMANCE = ModelRequestIntent(requested_model="openai:gpt-6.1-sol", requested_effort="low")
NO_REQUEST = ModelRequestIntent()
UNAVAILABLE = ModelRequestIntent(unavailable_model=True)
CASES = (
    Case("natural language", "Use Opus to fix the login bug", OPUS),
    Case("inline command", "Fix the login bug. /model Opus", OPUS),
    Case("typo", "/model Oppus\nFix the login bug", OPUS),
    Case("canonical ID", "Use anthropic:claude-opus-5-5 for this task", OPUS),
    Case("correction", "Use Sonnet; actually use Opus to fix the bug", OPUS),
    Case("performance tier", "Use the performance model to fix the bug", PERFORMANCE),
    Case("perf alias", "Use perf to fix the bug", PERFORMANCE),
    Case("performance typo", "use perfromance model", PERFORMANCE),
    Case("tier command", "/model:perf Fix the bug", PERFORMANCE),
    Case("tier correction", "Use Opus; actually use perf to fix the bug", PERFORMANCE),
    Case("model correction", "Use performance; actually use Opus to fix the bug", OPUS),
    Case("tier conflict", "Use perf or Opus to fix the bug", NO_REQUEST),
    Case("performance work", "Fix performance of model selection", NO_REQUEST),
    Case("tier comparison", "Compare perf and balanced models", NO_REQUEST),
    Case("tier negation", "Do not use the performance model", NO_REQUEST),
    Case("quoted tier", 'Explain what "use perf" means', NO_REQUEST),
    Case("forwarded tier", 'Bob wrote: "use perf". Summarize his request.', NO_REQUEST),
    Case("tier code literal", 'Add a test for parsing the string "/model:perf"', NO_REQUEST),
    Case("quality preference", "Use a better model to fix this", NO_REQUEST),
    Case("no choice", "Fix the login bug", NO_REQUEST),
    Case("task subject", "Fix the Opus integration", NO_REQUEST),
    Case("comparison", "Compare Opus and Sonnet latency", NO_REQUEST),
    Case("ambiguous family", "Use Claude to fix the bug", NO_REQUEST),
    Case("conflicting choices", "Use Opus or Sonnet to fix this", NO_REQUEST),
    Case("negation", "Do not use Opus. Fix the login bug", NO_REQUEST),
    Case("quoted request", 'Explain what the instruction "use Opus" means', NO_REQUEST),
    Case(
        "forwarded request",
        'Bob wrote: "use Opus to fix login". Summarize his request.',
        NO_REQUEST,
    ),
    Case("code literal", 'Add a test for parsing the literal string "/model Opus"', NO_REQUEST),
    Case("Auto", "Use Auto to fix the login bug", NO_REQUEST),
    Case("speed preference", "Use the fastest model to fix the bug", NO_REQUEST),
    Case("unknown model", "Use nonexistent-model-xyz to fix the bug", UNAVAILABLE),
    Case("unavailable version", "Use Opus 4.7 to fix the bug", UNAVAILABLE),
    Case("disabled model", "Use Fable to fix the bug", UNAVAILABLE),
    Case(
        "enabled model",
        "Use Fable to fix the bug",
        ModelRequestIntent(requested_model="anthropic:claude-fable-5-1"),
        fable_enabled=True,
    ),
)


async def main() -> int:
    failures = 0
    for case in CASES:
        actual = await infer_requested_model(
            messages=[HumanMessage(content=case.request)],
            requested_models=available_requested_models(fable_enabled=case.fable_enabled),
            performance_model=("openai:gpt-6.1-sol", "low"),
        )
        passed = actual == case.expected
        failures += not passed
        print(f"{'PASS' if passed else 'FAIL'} {case.name}: {actual}")
        if not passed:
            print(f"  expected: {case.expected}")
        if actual is None:
            print("Classifier unavailable or below confidence threshold; stopping evaluation.")
            return 1
    print(f"{len(CASES) - failures}/{len(CASES)} cases passed")
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
