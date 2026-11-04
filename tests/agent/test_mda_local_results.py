"""``mda/rt/local_results.py`` answers byte for byte as the backend's tools MDA runs itself.

MDA runs the tools the backend marks (``ToolSpec.runtime_runs``) rather than calling their
operation, and keeps its own copy of what they answer because ``mda/`` never imports the
backend (AGENTS.md). The model, the reply rules and the review page read those results,
so the copy must not drift; it is standard library only so this test can load it.
"""

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from langchain_core.messages import ToolCall, ToolMessage
from langchain_core.tools import BaseTool
from langchain_core.tools import tool as as_tool
from langgraph.types import Command

from openswe.bridge.cli_result import cli_result
from openswe.prompts import prompt
from openswe.slack.tools.no_reply_needed import slack_no_reply_needed
from openswe.tools.note_on_diff import note_on_diff
from openswe.tools.propose_pr_review import propose_pr_review
from openswe.tools.propose_review_comment import propose_review_comment
from openswe.tools.record_human_input import record_human_input
from openswe.tools.sandbox_output import chunk_output_as_jsonl
from openswe.tools.web_search import _bounded_inline_results

_ROOT = Path(__file__).parents[2]


def _mda_copy() -> ModuleType:
    path = _ROOT / "mda" / "rt" / "local_results.py"
    spec = importlib.util.spec_from_file_location("mda_local_results", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MDA = _mda_copy()
_DIFF_LINES: list[dict[str, Any]] = [
    {"file": "/src/app.py", "line": 12, "body": "  Rename this.  "},
    {"file": "src/app.py", "line": 12, "body": "Range", "start_line": 10, "side": "LEFT"},
    {"file": " / ", "line": 3, "body": "No file"},
    {"file": "a.py", "line": 3, "body": "Backwards", "start_line": 5},
    {"file": "a.py", "line": 3, "body": "   "},
    {"file": "a.py", "line": 3, "body": "x" * 10_001},
]


async def _backends(tool: Any, arguments: dict[str, Any]) -> str | Command:
    """What the backend's ``tool`` answers a call with, as its operation hands it to MDA."""
    built: BaseTool = as_tool(tool)
    result = await built.ainvoke(
        ToolCall(name=built.name, args=arguments, id="c1", type="tool_call")
    )
    if isinstance(result, Command):
        return result
    assert isinstance(result, ToolMessage)
    return result.text


def _mdas(answer: object) -> str:
    return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)


@pytest.mark.parametrize(
    ("backend", "copy", "arguments"),
    [
        *((note_on_diff, _MDA.note_on_diff, lines) for lines in _DIFF_LINES),
        *((propose_review_comment, _MDA.propose_review_comment, lines) for lines in _DIFF_LINES),
        (propose_pr_review, _MDA.propose_pr_review, {"event": "APPROVE"}),
        (propose_pr_review, _MDA.propose_pr_review, {"event": "COMMENT", "body": "  Looks good  "}),
        (propose_pr_review, _MDA.propose_pr_review, {"event": "REQUEST_CHANGES", "body": " "}),
        (propose_pr_review, _MDA.propose_pr_review, {"event": "COMMENT", "body": "x" * 10_001}),
        (cli_result, _MDA.cli_result, {"stdout": "ok", "exit_code": 0}),
        (cli_result, _MDA.cli_result, {"stdout": "", "exit_code": 256, "unchanged": True}),
    ],
)
async def test_a_tool_mda_runs_answers_as_the_backends(
    backend: Any, copy: Any, arguments: dict[str, Any]
) -> None:
    assert _mdas(copy(**arguments)) == await _backends(backend, arguments)


@pytest.mark.parametrize(
    ("reason", "confirmation"),
    [
        ("Only an emoji reaction.", "The user cannot see anything I do not send to Slack."),
        ("Thanks only", "the user  cannot see anything i do not send to slack"),
        (" ", "The user cannot see anything I do not send to Slack."),
        ("Nothing to add", "Nobody is waiting."),
    ],
)
async def test_closing_a_slack_turn_answers_as_the_backend(reason: str, confirmation: str) -> None:
    arguments = {"reason": reason, "confirmation": confirmation}
    copy = _MDA.slack_no_reply_needed(
        reason, confirmation, next_step=_mda_prompt("runs/slack-turn-closed")
    )
    assert _mdas(copy) == await _backends(slack_no_reply_needed, arguments)


@pytest.mark.parametrize("summary", ["  Needs the export button.  ", "   ", "x" * 2_500])
async def test_a_human_input_summary_is_recorded_as_the_backend_records_it(summary: str) -> None:
    text, kept = _MDA.record_human_input(summary)
    command = await _backends(record_human_input, {"summary": summary})

    assert isinstance(command, Command) and isinstance(command.update, dict)
    [message] = command.update["messages"]
    assert (text, kept) == (message.content, command.update["human_input_summary"])


@pytest.mark.parametrize("length", [0, 1, 499, 500, 1_201])
def test_a_large_result_is_offloaded_in_the_backends_chunks(length: int) -> None:
    text = ("ä" + "x" * 9) * (length // 10) + "y" * (length % 10)
    assert _MDA.chunk_output_as_jsonl(text) == chunk_output_as_jsonl(text)


@pytest.mark.parametrize("length", [100_000, 100_001])
def test_search_results_too_large_to_offload_are_cut_as_the_backend_cuts_them(length: int) -> None:
    text = "x" * length
    assert _MDA.bounded_inline_results(text) == _bounded_inline_results(text)


def _mda_prompt(name: str) -> str:
    return (_ROOT / "mda" / "prompts" / f"{name}.md.jinja").read_text(encoding="utf-8").strip()


def test_the_turn_closed_prompt_mda_answers_with_is_the_backends() -> None:
    assert _mda_prompt("runs/slack-turn-closed") == prompt("runs/slack-turn-closed")
