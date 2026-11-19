# Trajectory scores

Scores a finished Robin run from its LangSmith trace, for the slow-trajectory failure modes tracked in [RBN-211](https://linear.app/langchain/issue/RBN-211).
The scores read only the trace, so the same numbers compare a production thread, a replayed case and a benchmark trial (RBN-228).

| Score | What it catches |
|---|---|
| `wall_seconds` | How long the turn took, first call to last |
| `blocking_execute_seconds` | Time spent waiting on `execute` calls longer than 60s in the foreground |
| `longest_execute_seconds` | The single longest `execute` |
| `long_sleeps` | `execute` calls with a fixed `sleep` over 120s, where a `monitor` or wakeup belongs |
| `used_background` | Whether the run used `background_execute` or `monitor` at all |
| `slow_eval_seconds` | Time in code-mode `eval` calls longer than 60s (JSON-as-text stalls, serial fan-out) |
| `summary_seconds` | Time spent compacting the conversation |
| `most_repeated_failure` | The most times one tool failed in a row with the same arguments |
| `replied` | Whether the run ever answered: `slack_reply`, or a model message with text and no tool calls |
| `first_reply_seconds` | When that first answer came, `-1` if never |

## Run

```bash
uv run python -m evals.trajectories.score <trace-id> [<trace-id> ...]
uv run python -m evals.trajectories.score --thread <thread-id> --since 2026-11-10
```

`--feedback` saves the scores on the root run as `trajectory.<score>` feedback and replaces those scores when rerun.
If Robin never replied, its latency feedback has no numeric score, so it does not skew timing aggregates; use `trajectory.replied` to count unanswered runs.
The project defaults to Robin's production project; pass `--project` for staging or a benchmark run.
