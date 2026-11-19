"""Scores a finished Robin run from its trace, for the failure modes in RBN-211.

Each score reads only the trace, so it works the same on a production thread, a replayed
case or a benchmark trial: what changes between runs is the agent, not the measuring.
"""

import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Self

from langsmith.schemas import Run

# A step the agent waits on in the foreground for longer than this blocked the turn.
BLOCKING_SECONDS: Final = 60.0
# Longer than this, a fixed sleep should have been a monitor or a wakeup.
LONG_SLEEP_SECONDS: Final = 120
_SLEEP: Final = re.compile(r"\bsleep\s+(\d+)")
_SUMMARY: Final = "## SESSION INTENT"
_REPLY_TOOLS: Final = frozenset({"slack_reply"})
_BACKGROUND_TOOLS: Final = frozenset({"background_execute", "monitor"})


@dataclass(frozen=True)
class Step:
    """One model or tool call in a run's trace."""

    name: str
    kind: str
    started: datetime
    seconds: float
    inputs: Mapping[str, object]
    outputs: Mapping[str, object]
    raised: bool
    agent_type: str | None

    @classmethod
    def of(cls, run: Run) -> Self | None:
        if run.run_type not in ("llm", "tool") or run.end_time is None:
            return None
        agent_type = run.metadata.get("ls_agent_type")
        return cls(
            name=run.name,
            kind=run.run_type,
            started=run.start_time,
            seconds=(run.end_time - run.start_time).total_seconds(),
            inputs=run.inputs or {},
            outputs=run.outputs or {},
            raised=run.error is not None,
            agent_type=agent_type if isinstance(agent_type, str) else None,
        )

    @staticmethod
    def _result_fields(result: object) -> Mapping[str, object]:
        if isinstance(result, str) and result.lstrip().startswith("{"):
            try:
                result = json.loads(result)
            except ValueError:
                logging.getLogger(__name__).debug("Tool output was not JSON", exc_info=True)
        return result if isinstance(result, Mapping) else {}

    def _tool_reported_failure(self, result: Mapping[str, object]) -> bool:
        if result.get("success") is False or result.get("status") == "error":
            return True
        if result.get("type") != "tool":
            return False
        artifact = result.get("artifact")
        if self.name == "execute" and isinstance(artifact, Mapping):
            code = artifact.get("exit_code")
            if isinstance(code, int) and code != 0:
                return True
        content = result.get("content")
        if isinstance(content, list):
            content = " ".join(
                str(block.get("text", "")) if isinstance(block, Mapping) else str(block)
                for block in content
            )
        return self._result_fields(content).get("success") is False

    @property
    def failed(self) -> bool:
        if self.raised or self.kind != "tool":
            return self.raised
        result = self._result_fields(self.outputs.get("output", self.outputs))
        update = result.get("update")
        if isinstance(update, Mapping) and isinstance(messages := update.get("messages"), list):
            return any(self._tool_reported_failure(self._result_fields(msg)) for msg in messages)
        return self._tool_reported_failure(result)

    @property
    def command(self) -> str:
        command = self.inputs.get("command")
        return command if isinstance(command, str) else ""

    @property
    def text(self) -> str:
        """What the step answered, flattened to text for matching."""
        return json.dumps(self.outputs, default=str)

    @property
    def is_summary(self) -> bool:
        return self.kind == "llm" and _SUMMARY in self.text

    @property
    def message(self) -> Mapping[str, object]:
        """The model's reply message, as LangSmith records a chat model's output."""
        generations = self.outputs.get("generations")
        if not isinstance(generations, list) or not generations or not generations[0]:
            return {}
        first = generations[0][0] if isinstance(generations[0], list) else generations[0]
        message = first.get("message") if isinstance(first, dict) else None
        if not isinstance(message, dict):
            return {}
        kwargs = message.get("kwargs")
        return kwargs if isinstance(kwargs, dict) else message

    @property
    def is_final_answer(self) -> bool:
        """A model call that answered in words and asked for no tool."""
        message = self.message
        if (
            self.kind != "llm"
            or self.raised
            or self.agent_type not in (None, "root")
            or self.is_summary
            or message.get("type") != "ai"
            or message.get("tool_calls")
        ):
            return False
        content = message.get("content")
        if isinstance(content, str):
            return bool(content.strip())
        return isinstance(content, list) and any(
            isinstance(part, dict) and part.get("type", "text") == "text" and part.get("text")
            for part in content
        )

    @property
    def longest_sleep(self) -> int:
        return max((int(seconds) for seconds in _SLEEP.findall(self.command)), default=0)


@dataclass(frozen=True)
class Trajectory:
    """A run's model and tool calls, in the order they started."""

    steps: tuple[Step, ...]

    @classmethod
    def of(cls, runs: Iterable[Run]) -> Self:
        steps = (step for run in runs if (step := Step.of(run)) is not None)
        return cls(tuple(sorted(steps, key=lambda step: step.started)))

    @property
    def wall_seconds(self) -> float:
        if not self.steps:
            return 0.0
        last = max(step.started.timestamp() + step.seconds for step in self.steps)
        return last - self.steps[0].started.timestamp()

    def _tools(self, *names: str) -> list[Step]:
        return [step for step in self.steps if step.kind == "tool" and step.name in names]

    def scores(self) -> dict[str, float | int | bool]:
        """What LangSmith records as feedback on the run, one key per failure mode."""
        executes = self._tools("execute")
        blocking = [step for step in executes if step.seconds > BLOCKING_SECONDS]
        evals = [step for step in self._tools("eval") if step.seconds > BLOCKING_SECONDS]
        summaries = [step for step in self.steps if step.is_summary]
        replies = [step for step in self._tools(*_REPLY_TOOLS) if not step.failed]
        answered = replies or [step for step in self.steps if step.is_final_answer]
        return {
            "wall_seconds": round(self.wall_seconds),
            "blocking_execute_seconds": round(sum(step.seconds for step in blocking)),
            "longest_execute_seconds": round(max((s.seconds for s in executes), default=0)),
            "long_sleeps": sum(s.longest_sleep > LONG_SLEEP_SECONDS for s in executes),
            "used_background": bool(self._tools(*_BACKGROUND_TOOLS)),
            "slow_eval_seconds": round(sum(step.seconds for step in evals)),
            "summary_seconds": round(sum(step.seconds for step in summaries)),
            "most_repeated_failure": self.most_repeated_failure,
            "replied": bool(answered),
            "first_reply_seconds": round(
                answered[0].started.timestamp() - self.steps[0].started.timestamp()
            )
            if answered
            else -1,
        }

    @property
    def most_repeated_failure(self) -> int:
        """The most times one tool failed in a row with the same arguments."""
        most = run = 0
        previous: tuple[str, str] | None = None
        for step in self.steps:
            if step.kind != "tool":
                continue
            call = (step.name, json.dumps(step.inputs, sort_keys=True, default=str))
            if not step.failed:
                previous, run = None, 0
                continue
            run = run + 1 if call == previous else 1
            previous = call
            most = max(most, run)
        return most
