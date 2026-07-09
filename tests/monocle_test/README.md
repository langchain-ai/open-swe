# Open SWE behavioural tests (Monocle Test Tools)

Trace-based tests that lock in Open SWE's behaviour. Monocle records each run as a
structured trace -- the graph/agent invocation, every tool call, token usage, and
timings -- and each test asserts against that trace: which graph ran, which tools
it called, what it was asked, and its token/duration cost. A later prompt, model, or
tool change that regresses the behaviour fails here.

## Layout

- `test_openswe.py` — the suite: two offline tests + one live test
- `conftest.py` — Monocle setup, `.env` loading, and `run_openswe()` (used by the live test)
- `traces/` — recorded good-trace fixtures the offline tests replay
- `pytest.ini` — scopes this suite as its own rootdir so pytest does not load the
  app-importing parent `tests/conftest.py`
- `requirements.txt` — dependencies

## Tests

Each offline test loads its trace by file
(`with_trace_source("file", id=..., trace_path=...)`) and asserts structure + budget.

| Test | Scenario | Graph | What it shows |
|---|---|---|---|
| `test_openswe_greet_helper` | Add a `greet(name)` helper + export it | `agent` | mutating edit loop via the `execute` shell tool, negatives (no web/repo-search), input, budget |
| `test_openswe_reviewer_graph_qa` | What does the reviewer graph do? | `chat` | read-only research: search + web tools, negatives (no edit/write), input, budget |
| `test_openswe_reviewer_qa_live` | (live) reviewer-graph Q&A | `chat` | live run, structure + budget only |

The two offline tests contrast the mutating coding path (`agent` graph: edits files,
runs commands) with the read-only Q&A path (`chat` graph: search + web, never writes).
In the recorded `agent` run the change went through the `execute` shell tool, so that
test asserts `execute`/`ls` rather than `edit_file`/`write_file`. Budgets are the real
numbers measured from each trace (~260.7k tokens / ~88.5s for greet, ~92.9k / ~35.1s
for reviewer), rounded up with headroom. The live test drives the read-only `chat`
graph end-to-end and asserts structure + budget only, since output varies run to run.

## Run

```bash
pip install -r requirements.txt

pytest tests/monocle_test/ -k "not live"   # offline, no network, no keys
pytest tests/monocle_test/                 # includes the live run
```

The live test drives Open SWE's read-only `chat` graph (no sandbox, no commit/push
/PR -- no side effects). Run it in an environment where Open SWE itself is installed;
it needs a GitHub App installation token for the target repo (bot-token mode) and
reachable web tools, so it skips unless `OPENSWE_RUN_LIVE=1`. Set the target repo with
`OPENSWE_CHAT_REPO_OWNER` / `OPENSWE_CHAT_REPO_NAME`.

## Add your own test

1. Run Open SWE under Monocle and capture a trace of a run you're happy with
   (Monocle writes trace JSON to `.monocle/` by default).
2. Move it into `traces/` and load it with
   `monocle_trace_asserter.with_trace_source("file", id="<trace_id>", trace_path="<path>")`.
3. Assert with the fluent API — `called_agent(...)`, `called_tool(...)`,
   `does_not_call_tool(...)`, `contains_input(...)`, `under_token_limit(...)`,
   `under_duration(..., span_type="workflow")` — then add it alongside the others.

## Evaluations (optional)

`test_openswe_greet_helper` and the live test carry a commented-out
`check_eval("hallucination", ...)` chain. Monocle can run evaluation checks against a
trace; set `OKAHU_API_KEY` and uncomment to enable. Get a key at https://www.okahu.ai.
