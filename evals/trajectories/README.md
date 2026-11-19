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

## Replay cases

`tasks/` holds [Harbor](https://www.harborframework.com) tasks rebuilt from slow production traces, one folder per failure mode.
Each has an instruction, a Docker environment, a `tests/test.sh` grader and a reference `solution/`.

| Task | Failure mode | What a good run does |
|---|---|---|
| `long-build` | Blocking on a ~6 min build (RBN-212) | Fixes the bug, runs the build in the background and checks back |
| `repo-search` | `grep -r` over vendored code and a generated patch (RBN-227) | Uses `rg`/`git grep`; decoys in ignored files give a wrong answer |
| `answer-first` | Slow setup before a simple question (RBN-220) | Answers from the code without running `make setup` |

`harbor_agent.py` runs the deployed Robin agent on each task through `oswe run`, with the task's container as its sandbox (ported from Johannes's Terminal-Bench harness).
Point it at a deployment with a workspace API key:

```bash
export OPEN_SWE_BACKEND_URL=https://<staging-backend>
export OPEN_SWE_API_KEY=osk_...
PYTHONPATH=. uvx --from harbor harbor run -p evals/trajectories/tasks \
  -a evals.trajectories.harbor_agent:Robin -n 3
```

The grader scores the outcome.
Score the trajectory of each trial's thread with `evals.trajectories.score --thread <id> --project <staging project>`.
Commands over ~10 min through the sandbox bridge lose their reply (RBN-213), so `long-build` stays under that.

### In CI

`.github/workflows/robin_trajectory_evals.yml` runs the tasks on demand (Actions → "Robin: replay trajectory evals") against staging, or preview to measure a PR labeled `robin-preview` before it merges, and writes each trial's reward and trajectory scores to the job summary (`python -m evals.trajectories.report <job-dir>`).
The CLI in each task container signs in with a GitHub OIDC token for that deployment only, minted on the runner by `oidc_broker.py` (the container never holds the job's token-request credentials), so a workspace admin must first allow `langchain-ai/langchainplus` to start threads (`may_start_threads`, set with Robin's `configure_repository` tool).
Harbor's LangSmith plugin also records each run as its own experiment on the `robin-trajectory-replays` dataset, with each trial's reward and latency, so runs compare side by side under Datasets & Experiments.
The experiments have no token or cost figures, since those live in Robin's own traces rather than in Harbor.
Both use the workspace key Robin's MDA deploys use, loaded from 1Password; it stays on the runner.
