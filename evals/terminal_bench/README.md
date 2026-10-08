# Terminal-Bench

Runs Open SWE on [Terminal-Bench](https://www.tbench.ai) through
[Harbor](https://www.harborframework.com). The agent installs `oswe` in each task
container and runs `oswe run "<task>"` there, so the deployed Open SWE agent works
with the container as its sandbox. Harbor's verifier then scores the result.

## Run

Requires Docker, or a cloud environment such as `--env daytona`.

```bash
export OPEN_SWE_BACKEND_URL=https://open-swe.example.com
export OPEN_SWE_API_KEY=osk_...   # workspace API key

PYTHONPATH=. uvx --from harbor==0.24.0 harbor run \
  -d terminal-bench/terminal-bench@4.0.0 \
  -a evals.terminal_bench.agent:OpenSWE \
  -m anthropic/claude-sonnet-5 --ak effort=high \
  -l 10 -n 4
```

- `-m` is an Open SWE model id with `/` in place of `:`. Without `-m`, the workspace default model is used.
- `-l` caps the number of tasks and `-n` sets concurrency. Use `-i <glob>` to pick tasks.
- Each trial is a `system` thread on the deployment. Follow it on the dashboard.
