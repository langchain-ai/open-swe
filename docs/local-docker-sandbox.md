# Local Docker Sandbox Guide

This guide explains how to run the Open SWE sandbox locally using Docker. It covers the
sandbox image, Docker Compose workflow, and the environment variables that control how the
agent interacts with the local Docker daemon. Use it alongside the repository quickstart and
API key setup guides when configuring a new machine.

## Prerequisites

- **Docker Engine 24+** (or Docker Desktop on macOS/Windows) with the Docker Compose plugin
  available as `docker compose`.
- **Sufficient resources** for nested workloads. The default sandbox allocates 2 CPUs, 2 GiB of
  RAM, and up to 512 processes per task. Increase these limits if your tasks require more.
- **File sharing** enabled for the directory that contains your cloned repository. Docker Desktop
  users must add the repository root to the allowed share list.
- **Access to the Docker socket**. The agent mounts `/var/run/docker.sock` so the container can
  create child sandboxes. Linux users should belong to the `docker` group or run the Compose
  stack with elevated privileges.

## Quickstart

1. **Clone the repository and install dependencies.**
   ```bash
   git clone https://github.com/langchain-ai/open-swe.git
   cd open-swe
   yarn install
   ```
2. **Build (or update) the sandbox image.** Run either `yarn sandbox:build` or pull the published
   image. Both commands reference [`Dockerfile.sandbox`](../Dockerfile.sandbox).
   ```bash
   # build locally
   yarn sandbox:build

   # or pull the shared image
   docker pull ghcr.io/langchain-ai/open-swe/sandbox:latest
   ```
3. **Start the local stack.** The [`compose.local.yaml`](../compose.local.yaml) file launches the
   agent and UI together. Use the provided scripts from [`package.json`](../package.json).
   ```bash
   # start in the background
   yarn stack:up

   # follow the logs
   docker compose -f compose.local.yaml logs -f
   ```
4. **Visit the services.**
   - LangGraph agent API: http://localhost:2024
   - Next.js UI: http://localhost:3000
5. **Stop the stack when finished.**
   ```bash
   yarn stack:down
   ```

## Building the Sandbox Image

The dedicated [`Dockerfile.sandbox`](../Dockerfile.sandbox) provisions Node.js 20, Yarn 3.5.1,
Git, Python, and other build tools required by sandbox tasks. You can:

- **Build locally** for iterative testing:
  ```bash
  docker build -f Dockerfile.sandbox -t open-swe-sandbox:local .
  ```
- **Tag the image for reuse**:
  ```bash
  docker tag open-swe-sandbox:local ghcr.io/<org>/open-swe/sandbox:dev
  ```
- **Use the published image** maintained by LangChain:
  ```bash
  docker pull ghcr.io/langchain-ai/open-swe/sandbox:latest
  ```

Set `LOCAL_SANDBOX_IMAGE` (see below) to point the agent at your preferred tag.

## Compose Workflow

[`compose.local.yaml`](../compose.local.yaml) builds the monorepo image once and then starts two
services:

- `agent`: Runs `yarn workspace @openswe/agent dev` and exposes port `2024`.
- `ui`: Runs `yarn workspace @openswe/web dev` and exposes port `3000`.

Both services share the same image build context and mount the repository into `/workspaces`. The
agent service also mounts the host Docker socket so it can create nested sandboxes. Helpful
commands include:

- `docker compose -f compose.local.yaml build` – rebuild the monorepo image.
- `docker compose -f compose.local.yaml up --detach` – start the stack without the helper script.
- `docker compose -f compose.local.yaml down --volumes` – stop the stack and remove anonymous
  volumes.

Use the `WORKSPACES_ROOT` environment variable if your repository lives outside the project root;
for example, `WORKSPACES_ROOT=$PWD/.. yarn stack:up` to mount a parent directory of workspaces.

## Environment Variables

The Compose file passes several variables into the agent container. The most common ones are
summarized below.

| Variable | Purpose |
| --- | --- |
| `OPEN_SWE_LOCAL_MODE` | Enables local sandbox orchestration. Leave `true` for Docker-based workflows. |
| `OPEN_SWE_LOCAL_PROJECT_PATH` | Host path to the repositories Open SWE can access. Defaults to `/workspaces`. |
| `OPEN_SWE_PROJECT_PATH` | Explicit host repository path. Set when targeting a specific project directory. |
| `LOCAL_SANDBOX_IMAGE` | Docker image used for sandbox tasks (defaults to `ghcr.io/langchain-ai/open-swe/sandbox:latest`). |
| `LOCAL_SANDBOX_MEMORY` / `LOCAL_SANDBOX_CPUS` / `LOCAL_SANDBOX_PIDS` | Override default sandbox resource limits (2 GiB RAM, 2 CPUs, 512 PIDs). |
| `LOCAL_SANDBOX_NETWORK` | Controls network access (see [Network Controls](#network-controls)). |
| `LOCAL_SANDBOX_TIMEOUT_SEC` | Maximum command runtime inside the sandbox (default 900 seconds). |
| `SANDBOX_ROOT_DIR` | Root directory inside the agent container where repositories are staged. Defaults to the local working directory in local mode. |
| `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL` / `GIT_COMMITTER_NAME` / `GIT_COMMITTER_EMAIL` | Identity used for automatic commits created during sandbox runs. |
| `SKIP_CI_UNTIL_LAST_COMMIT` | Controls whether automatically created commits append `[skip ci]` (defaults to `true`). |
| `AZURE_OPENAI_*`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | API credentials for the models Open SWE will call. |

Refer to [`compose.local.yaml`](../compose.local.yaml) and the sandbox helper in
[`apps/open-swe/src/utils/sandbox.ts`](../apps/open-swe/src/utils/sandbox.ts) for more details.

## Network Controls

The agent inspects `LOCAL_SANDBOX_NETWORK` to decide whether to attach a network to the sandbox
container. Any value other than an empty string or one of `none`, `false`, `off`, or `disabled`
enables networking and is forwarded to Docker as the desired network mode. For example:

- `LOCAL_SANDBOX_NETWORK=bridge` – attach to the default bridge network.
- `LOCAL_SANDBOX_NETWORK=host` – use the host network (Linux only).
- `LOCAL_SANDBOX_NETWORK=none` – explicitly disable all network access.

Leave the variable unset to run offline sandboxes.

## Host Commit Workflow

When Open SWE runs in local mode it mounts your repository into the sandbox and automatically
commits successful changes back on the host.

1. The agent configures Git inside the sandbox by marking the repository as a safe directory and
   populating the configured author/committer identity.
2. After each successful command, the sandbox checks the host repository for staged changes. If
   any exist, it stages and commits them using the `OpenSWE auto-commit #<n> [skip ci]` message.
3. The counter resets when the stack restarts; commits include `[skip ci]` unless you disable it by
   setting `SKIP_CI_UNTIL_LAST_COMMIT=false`.

You can customise the commit metadata with the `GIT_*` variables mentioned above. See
[`apps/open-swe/src/utils/sandbox.ts`](../apps/open-swe/src/utils/sandbox.ts) for the exact workflow.

## Troubleshooting

### Docker socket permissions

If the agent logs `permission denied while trying to connect to the Docker daemon socket`, ensure
that your user can access `/var/run/docker.sock`. On Linux, add yourself to the `docker` group and
re-login:

```bash
sudo usermod -aG docker "$USER"
newgrp docker
```

Alternatively, start the Compose stack with elevated privileges.

### Windows path handling

Docker Desktop on Windows requires additional configuration when mounting the Docker socket and
workspaces:

- Enable WSL 2 integration for the distribution that hosts your repository.
- Ensure the repository directory is shared in Docker Desktop **Settings → Resources → File
  Sharing**.
- When running commands from PowerShell or CMD, set `COMPOSE_CONVERT_WINDOWS_PATHS=1` so that the
  `/var/run/docker.sock` mount resolves correctly.

### Git safe.directory

If Git refuses to operate on mounted repositories because they originate outside the container,
add the path to the global safe directory list:

```bash
git config --global --add safe.directory "$(pwd)"
```

The agent already runs a similar command inside the sandbox, but you may need to configure it on
your host system for manual Git operations.

