# Smoke Results — 2026-10-20

This is the historical run before graph-state patch collection and execution
environment guidance were added. Its two-run extraction flow and environment
failures describe that version. Current behavior and validation are recorded in
[LOCAL_RESULTS.md](LOCAL_RESULTS.md); no post-change model benchmark is claimed.

First end-to-end run of `evals/agent/` against a live agent graph. The goal was
to prove the pipeline, not to score well: drive the real coding agent on a real
repository, extract its diff, and judge it deterministically.

## Environment

| Piece | Value |
|---|---|
| Agent graph | local `langgraph dev` (port 2024), `SANDBOX_TYPE=local` |
| Model | `openai:qwen3.5:9b` via local Ollama (OpenAI-compatible endpoint) |
| Surface | `desktop` (no GitHub App credentials available) with an allowlisted local project dir |
| Instance | `evals/agent/instances/smoke.jsonl` — add `zero_pad` to `pallets/itsdangerous` @ `672971d` |
| Repo transport | `https://ghfast.top/...` mirror (github.com unreachable from this host) |
| Judge | local `git` + `.venv` python + pytest, real clone + `python -m pip install -e .` |

## What the pipeline proved

1. `build_dataset.py` uploads instances to LangSmith (`openswe-agent-smoke`).
2. `run_eval.py` drives the agent graph over `langgraph_sdk`, two runs per
   thread (solve, then extract the diff against the base commit).
3. The agent executed for real: **35 model turns, 344.7 s wall clock**, and
   returned a final message.
4. `judge.py` ran the full deterministic path — clone at base commit, apply
   patch + `test_patch`, install, run the named pytest ids — and recorded the
   verdict in the LangSmith experiment.
5. Gold-patch validation (same judge path, reference solution) resolved the
   instance: 4/4 target tests pass, existing test unaffected. The instance is
   sound; scoring works in both directions.

## Outcome

`resolved = false`. The agent never landed a checkout: the sandbox's command
surface is POSIX and the local provider runs `cmd.exe`, so `rm`, `head`, `tail`,
`grep`, `pwd`, and `cd /D:` all failed (35 turns of adaptation, then it drifted
into writing a plan document). The 163-character final message was prose, the
judge's `extract_patch` correctly treated it as a no-change run, and every
`fail_to_pass` id went unreported. This is a capability finding about a 9B
model on a Windows local sandbox — not a harness failure. Run scored
benchmarks on Linux (see README "Known limitations").

## Bugs found and fixed while standing the pipeline up

1. `agent/desktop.py` — `os.getuid()` crashes on Windows; guarded, with a test
   for the default artifacts root (the old tests always set the env var, so
   the branch was never covered).
2. `agent/sandboxes/paths.py` — work-dir resolution was POSIX-only (leading-`/`
   filter, `test -d` probe, `pwd`/`$HOME`). Now accepts Windows drive-letter
   paths, probes with a cmd-compatible file create, and considers the backend's
   own `cwd` first. POSIX behavior and its tests are unchanged.
3. `agent/utils/model.py` + `agent/dashboard/options.py` — local Ollama models
   must be registered in `OPENAI_CHAT_COMPLETIONS_MODEL_IDS` to use Chat
   Completions instead of the Responses API (Ollama has no Responses API).
   Registered `openai:qwen3.5:9b`.
4. Eval harness — `AGENT_EVAL_PYTHON` now pins the judge's interpreter and its
   bin dir is prepended to PATH for repo `install_commands` (uv venvs ship no
   `pip.exe`); the extract prompt diffs against the base commit so a committing
   agent still yields a patch; `clone_url` (mirrors/self-hosted) and
   `run_source=desktop` + `local_project_dir` (no App credentials) were added.

## Artifacts

- LangSmith dataset: `openswe-agent-smoke` (`b0cf9c56-637e-4609-a84e-0afb33f86cce`)
- Experiment: `openswe-agent-baseline-3b3d7822`
- Eval thread: `01a15505-9b48-7412-bdce-bef97232c8f1` (kept via `--no-cleanup`)

## Reproduce

```bash
# server: PYTHONUTF8=1 SANDBOX_TYPE=local LOCAL_SANDBOX_ROOT_DIR=<dir> \
#   OPEN_SWE_LOCAL_PROJECTS_FILE=<allowlist.json> langgraph dev --allow-blocking
uv run python -m evals.agent.build_dataset \
    --instances evals/agent/instances/smoke.jsonl --dataset-name openswe-agent-smoke
AGENT_EVAL_PYTHON=<venv python> uv run python -m evals.agent.run_eval \
    --limit 1 --dataset-name openswe-agent-smoke \
    --model-id openai:qwen3.5:9b --run-source desktop \
    --local-project-dir <allowlisted dir> --no-cleanup
```
