# Routing Eval

Offline LangSmith eval for pre-routed mode ([OEP-0003](../../oeps/0003-pre-routed-mode.md)).
Each example is a task against `langchain-ai/open-swe`. The real agent runs on the real
models with the dashboard's defaults until it calls `exit_pre_routed_mode`; the run is
then cancelled and the decision is scored.

## Layout

```
evals/routing/
├── tasks.json          # 40 tasks with an expected route: fast, balanced, performance, none
├── build_dataset.py    # tasks.json → LangSmith dataset
├── config.toml         # defaults for run_eval
├── target.py           # starts one run, stops at the routing decision, returns the outcome
├── judge.py            # route scoring, title judge, exploration cost, aggregate
└── run_eval.py         # aevaluate entrypoint
```

## What is scored

| Feedback | Meaning |
|---|---|
| `route_match` | agent's route equals the label (`none` means answered without routing) |
| `route_distance` | tiers between agent and label; missing or timed out counts as 3 |
| `over_routed`, `under_routed` | direction of the miss |
| `title_format` | 3 to 8 words, under 80 chars, no trailing punctuation or markup |
| `title_quality` | Opus judge against the title rules the agent is given |
| `model_calls_before_exit`, `tool_calls_before_exit`, `pre_exit_*_tokens`, `seconds_to_decision` | what sizing the task cost |
| `rejected_tool_calls` | tool calls the pre-routed guard refused; should be 0 |

The summary reports route accuracy, a confusion list, timeouts, and mean cost.

## Prerequisites

- `LANGSMITH_API_KEY` and `ANTHROPIC_API_KEY` in the environment. The judge calls Anthropic
  directly.
- A running `agent` graph with model routing enabled for the eval identity. Local dev works:

  ```bash
  SANDBOX_TYPE=local uv run langgraph dev --no-browser
  ```

  The three routing profiles come from the team settings on that server, so set them on the
  admin page or accept the defaults. Every profile's provider needs a key in that server's
  environment. Cloning needs GitHub access the same way a normal dashboard run does.

## Run

```bash
uv run python -m evals.routing.build_dataset            # once
uv run python -m evals.routing.run_eval --limit 3       # smoke test
uv run python -m evals.routing.run_eval                 # full run, ~40 threads
```

Threads created by the eval are deleted afterwards unless `--no-cleanup` is passed. Each
run is cancelled at the routing decision, so the eval never edits the repository or opens
pull requests.

## Reading results

Compare experiments in LangSmith on `route_accuracy` first, then `over_routed` versus
`under_routed` to see which way the prompt leans, then `mean_pre_exit_input_tokens` and
`mean_tool_calls_before_exit` for what the sizing turn costs. A change to the pre-routed
prompt or the exit tool description is a good change when accuracy holds and cost falls.
