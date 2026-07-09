"""Trace-based behavioural tests for Open SWE, using Monocle Test Tools.

Each offline test loads a recorded trace (`with_trace_source("file", ...)`) and asserts
which graph/agent ran, which tools it called, the input, and token/duration cost. Two
graphs are contrasted: the `agent` graph (mutating coding loop) and the read-only `chat`
graph (search + web, no writes). One live test drives `chat` end-to-end (opt-in).

    pytest tests/monocle_test/ -k "not live"   # offline, no keys
    pytest tests/monocle_test/                 # includes the live run
"""
import os

import pytest
from monocle_test_tools import TraceAssertion

from conftest import TRACES, run_openswe

TRACE_GREET = {
    "id": "0da2dead4a407a22f34ad130a7a6292b",
    "path": str(TRACES / "monocle_trace_open-swe_0da2dead4a407a22f34ad130a7a6292b_2026-07-09_14.03.57.json"),
}
TRACE_REVIEWER = {
    "id": "8ab314415f622b15e9e151acb4f3feba",
    "path": str(TRACES / "monocle_trace_open-swe_8ab314415f622b15e9e151acb4f3feba_2026-07-09_14.05.47.json"),
}


# --- Offline: replay recorded good traces ---------------------------------

def test_openswe_greet_helper(monocle_trace_asserter: TraceAssertion):
    """agent graph -- add a greet(name) helper and export it (mutating edit loop).

    Real trace: ~260.7k total tokens, ~88.5s workflow. The coding agent ran the
    edit loop through the ``execute`` shell tool (execute x16, ls x2); it used no
    web/repo-search tools -- the editor stayed local to the sandbox.
    """
    monocle_trace_asserter.with_trace_source("file", id=TRACE_GREET["id"], trace_path=TRACE_GREET["path"])

    monocle_trace_asserter.called_agent("agent").contains_input("greet(name) helper")
    monocle_trace_asserter.called_tool("execute", min_count=1)
    monocle_trace_asserter.called_tool("ls")
    monocle_trace_asserter.does_not_call_tool("web_search")
    monocle_trace_asserter.does_not_call_tool("search_repo_code")
    monocle_trace_asserter.under_token_limit(400_000)
    monocle_trace_asserter.under_duration(180, span_type="workflow")

    # Eval layer (deferred -- set OKAHU_API_KEY and uncomment to enable):
    # monocle_trace_asserter.with_evaluation("okahu").check_eval("hallucination", "no_hallucination") \
    #     .check_eval("contextual_precision", "high_precision") \
    #     .check_eval("sentiment", "positive") \
    #     .check_eval("bias", "unbiased")


def test_openswe_reviewer_graph_qa(monocle_trace_asserter: TraceAssertion):
    """chat graph -- read-only Q&A: what does the reviewer graph do?

    Real trace: ~92.9k total tokens, ~35.1s workflow. The read-only research path
    ran (read_file, ls, grep, search_repo_code, read_repo_file, web_search,
    fetch_url); it never edits or writes -- proof the chat graph is read-only.
    """
    monocle_trace_asserter.with_trace_source("file", id=TRACE_REVIEWER["id"], trace_path=TRACE_REVIEWER["path"])

    monocle_trace_asserter.called_agent("chat").contains_input("reviewer graph")
    monocle_trace_asserter.called_tool("web_search")
    monocle_trace_asserter.called_tool("read_file", min_count=1)
    monocle_trace_asserter.called_tool("search_repo_code")
    monocle_trace_asserter.does_not_call_tool("edit_file")
    monocle_trace_asserter.does_not_call_tool("write_file")
    monocle_trace_asserter.under_token_limit(150_000)
    monocle_trace_asserter.under_duration(90, span_type="workflow")


# --- Live: drive the read-only chat graph end-to-end ----------------------
# The chat graph is read-only (no sandbox, no commit/push/PR), so running it live
# has no side effects. It needs a running LangGraph server context, a GitHub App
# installation token for the target repo (bot-token mode), plus reachable web
# tools; that infra is not guaranteed in a plain pytest run, so this is gated
# behind OPENSWE_RUN_LIVE=1 and skips by default.
_LIVE_REASON = (
    "Live chat-graph run needs a GitHub App installation token for the target repo "
    "and reachable web tools; set OPENSWE_RUN_LIVE=1 to exercise it."
)


def test_openswe_reviewer_qa_live(monocle_trace_asserter: TraceAssertion):
    """Live read-only code Q&A: what does the reviewer graph do? (research path)."""
    if os.environ.get("OPENSWE_RUN_LIVE") != "1":
        pytest.skip(_LIVE_REASON)

    monocle_trace_asserter.validator.test_workflow(
        run_openswe,
        {"test_input": (
            "What does the reviewer graph do and where is it defined?",
        )},
    )

    # Driven in-process (not via the langgraph server), so the invocation is not
    # named "chat" -- assert an agent ran + the request/answer shape instead.
    monocle_trace_asserter.called_agents(min_count=1)
    monocle_trace_asserter.contains_input("reviewer graph")
    monocle_trace_asserter.contains_any_output("review", "reviewer", "PR", "agent", "graph")
    monocle_trace_asserter.does_not_call_tool("write_file")
    monocle_trace_asserter.under_token_limit(900_000)
    monocle_trace_asserter.under_duration(200, span_type="workflow")

    # monocle_trace_asserter.with_evaluation("okahu").check_eval("hallucination", "no_hallucination") \
    #     .check_eval("contextual_precision", "high_precision") \
    #     .check_eval("sentiment", "positive") \
    #     .check_eval("bias", "unbiased")
