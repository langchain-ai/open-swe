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
| `pull-before-answer` | Pulling and setting up the repo before a question it can already answer (RBN-220, s08/d04) | Reads two files and answers; a fetch, pull, `make setup` or pip install before the answer scores 0, and the pull alone takes 150s |
| `linked-resource` | Searching elsewhere instead of opening the resource the user linked (RBN-220, d08) | Reads the linked app and answers; the repo has an undeployed cap, and a search outside the app before the answer scores 0 |
| `plain-answer` | Building an HTML artifact when a few bullets were asked for (RBN-220, s10/s12) | Writes two plain bullets; any new HTML, SVG or image file scores 0 |
| `retry-loop` | Retrying a search that always times out (RBN-219) | Stops after a failure or two and finds the thread through the PR's author; more than 3 searches scores 0 |
| `json-results` | Handling large JSON as text, as in code-mode stalls (RBN-215) | Parses each run and its double-encoded payload; text matching counts a decoy case |
| `ci-babysit` | Blocking on a CI watch while a job has already failed (RBN-214) | Polls CI, fixes the early lint failure, reruns, and reports once the rerun is green; a blocking watch runs out of time |
| `ci-check-back` | Promising to check back on CI with nothing set to wake it (RBN-265, s14) | Says CI is running, ends the turn, and comes back on a wakeup or background watch to report the green run; a foreground watch or a minute of shell sleep scores 0, and a promise with no wakeup never answers |
| `tool-discovery` | Brute-forcing instead of finding a better tool (RBN-225) | Finds the `warehouse` CLI from the repo docs; replaying the raw exports gives a wrong total |

Each task runs in a LangSmith sandbox of its own, which the `robin` CLI attaches to the deployed agent (`robin run --sandbox`), using the Terminal-Bench adapter (`evals/terminal_bench/harbor_adapter.py`).
Point it at a deployment with a service key (`lsv2_sk_…`) from the deployment's workspace, which both creates the sandboxes and starts the threads:

```bash
export ROBIN_EVAL_BACKEND_URL=https://staging.robin.langchain.dev
export ROBIN_EVAL_API_KEY=lsv2_sk_...
export LANGSMITH_API_KEY="$ROBIN_EVAL_API_KEY"
PYTHONPATH=. uvx --python 3.13 --from 'harbor[langsmith]==0.26.0' harbor run -p evals/trajectories/tasks -n 3 \
  -a evals.terminal_bench.harbor_adapter:RobinAgent -e evals.terminal_bench.harbor_adapter:RobinSandbox
```

The grader scores the outcome.
Score the trajectory of each trial's thread with `evals.trajectories.score --thread <id> --project <staging project>`.

### In CI

`.github/workflows/robin_trajectory_evals.yml` runs each task three times against staging every night at 08:00 UTC and posts each task's pass rate to #team-robin-alerts.
When preview deploys a PR labeled `robin-preview` that changes Robin's runtime or prompts (`open-swe/mda/`, `open-swe/openswe/resources/prompts/`), it runs the tasks against preview too and comments each task's pass rate on that PR, next to the last nightly's.
It also runs on demand (Actions → "Robin: replay trajectory evals") against staging, or preview to measure a PR labeled `robin-preview` before it merges, and writes each trial's reward and trajectory scores to the job summary (`python -m evals.trajectories.report <job-dir>`).
It loads a service key from 1Password (`CI secrets/robin-evals/staging_service_key`), which serves preview too, since staging and preview Robin share a workspace.
Harbor's LangSmith plugin also records each run as its own experiment on the `robin-trajectory-replays` dataset, with each trial's reward and latency, so runs compare side by side under Datasets & Experiments.
The experiments have no token or cost figures, since those live in Robin's own traces rather than in Harbor.
The report reads the traces with the workspace key Robin's MDA deploys use.
