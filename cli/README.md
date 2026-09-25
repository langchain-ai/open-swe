# oswe CLI

`oswe` starts a normal cloud Open SWE agent on a remote deployment and makes
the current directory on the machine running it that agent's sandbox. The remote agent's
shell commands, file uploads and file downloads all execute here.

The deployment cannot dial that machine, so the CLI long-polls the backend for
sandbox requests, runs them locally, and posts the results back.

> The agent runs commands **unsandboxed**, as you, in whatever directory you
> started it from. It can read, write and delete anything you can.

## Install

Every desktop release, stable and nightly, ships `oswe` twice: inside the app
at `Open SWE.app/Contents/Resources/bin/oswe`, and as standalone downloads for
`darwin-arm64`, `darwin-x64`, `linux-arm64` and `linux-x64` with an
`oswe-SHA256SUMS` file. The macOS binaries are signed and notarized.

```sh
curl -fsSL https://github.com/langchain-ai/open-swe/releases/latest/download/oswe-darwin-arm64.tar.gz | tar -xz
sudo mv oswe /usr/local/bin/
```

`oswe --version` prints the desktop release it came from.

## Build

```sh
make cli            # -> cli/dist/oswe
```

Requires [Bun](https://bun.com/docs/installation). The binary embeds its own
runtime, so the machine that runs it needs neither Bun nor Node. Copy it
anywhere on your `PATH`:

```sh
cp cli/dist/oswe /usr/local/bin/
```

## Authenticate

The CLI uses the first credential it finds:

1. **`OPEN_SWE_API_KEY`** — a workspace API key (`osk_…`) an admin minted.
2. **GitHub Actions** — inside a job with `permissions: id-token: write`, the
   job's own OIDC token, requested with the backend URL as its audience
   (override with `OPEN_SWE_OIDC_AUDIENCE`). An admin must first let the
   repository start threads in its workspace's settings.
3. **A person's session** — `OPEN_SWE_SESSION`, or the one `oswe login`
   stored.

An API key and a workflow are machines: their threads are always `system`
threads. A person's threads are `workspace` threads unless `--visibility
private` is passed.

### Sign in as a person

```sh
oswe login --backend https://dev.open-swe.langchain.dev
```

This is the desktop app's sign-in: your browser opens the GitHub login the
dashboard uses, a loopback port catches the handoff, and PKCE S256 exchanges it
for the same session the desktop app stores. Requests then carry it as the
dashboard's own `osw_session` cookie. The session and backend URL live in
`~/.open-swe/config.json` (mode 0600), and `oswe logout` deletes the file.

### Run in GitHub Actions

```yaml
permissions:
  contents: read
  id-token: write
steps:
  - uses: actions/checkout@v4
  - run: oswe run "is the test suite green?"
    env:
      OPEN_SWE_BACKEND_URL: https://open-swe.example.com
```

### Backend

| Variable | Effect |
|---|---|
| `OPEN_SWE_BACKEND_URL` | the backend to call |
| `OPEN_SWE_DESKTOP_URL` | the same, checked second |

With neither set, the backend comes from `~/.open-swe/config.json`, then
from the backend the desktop app was last pointed at
(`desktop-config.json` in its application-support directory), then
`http://localhost:2024` — the desktop app's own development default.

The binary never reads a `.env`. It is compiled with
`--no-compile-autoload-dotenv`, because it runs inside your repository and a
`.env` there would otherwise enter both its environment and the agent's shell.

## Run

```sh
cd ~/code/my-project
oswe run "add retries to the upload path"
```

What happens:

1. A sandbox bridge is registered for the current directory and starts serving
   the remote agent's requests. Every run gets its own bridge, so two runs in
   one directory never serve each other's requests. The thread's bridge is
   remembered in `~/.open-swe/bridges.json` for `--thread`.
2. A thread is created on the deployment, with `origin` repo detected from
   `git remote get-url origin`, and its dashboard URL is printed to stderr.
3. The agent works. Nothing it says or runs is printed; follow it on the
   dashboard.
4. The agent ends the run by calling `cli_result` with `stdout` and an
   `exit_code`. The CLI prints that `stdout` verbatim as its only output on
   stdout, releases the bridge, and exits with that code.

So a run composes like any other command:

```sh
oswe run "is the test suite green?" && echo passing
git diff | oswe run "review this diff" > review.txt
```

The agent reports exit codes the way `grep` and `test` do: 0 when the task was
done or the answer is yes, 1 when it failed or the answer is no, 2 when it could
not tell. A run that fails, or ends without calling `cli_result`, prints the
reason to stderr and exits 1. The server re-prompts the agent twice before giving up on a
missing result.

Ctrl-C once cancels the run, releases the bridge and exits. Ctrl-C twice exits
immediately.

Options:

| Flag | Meaning |
| --- | --- |
| `--thread <id>` | Continue a thread oswe started on this machine, from the directory it serves. Refused while another oswe process is serving it. |
| `--model <id>` | Agent model id. The backend only honors it together with `--effort`. |
| `--effort <name>` | Reasoning effort for `--model`. |
| `--visibility private` | Start a private thread instead of a workspace one. People only. |

Piped input is attached below the prompt inside `<stdin>` tags, or is the whole
prompt when no arguments are given. Stdin is only read when it is a pipe or a
redirected file, so a CI runner's open stdin never blocks a run.

## Environment the agent gets

The child shell inherits your environment minus anything whose name ends in
`_API_KEY`, `_TOKEN`, `_SECRET` or `PASSWORD` — except `GITHUB_TOKEN` and
`GH_TOKEN`, so `gh` and `git` keep working. `GIT_TERMINAL_PROMPT=0`, `CI=1` and
`PAGER=GIT_PAGER=cat` are set so nothing waits on a terminal.

Commands time out after 300 s (killed with `SIGTERM`, then `SIGKILL`), and
output is capped at 1 MiB, keeping the first and last 512 KiB.

## Development

```sh
pnpm install --filter open-swe-cli   # from the repository root
pnpm --filter open-swe-cli run check # tsc --noEmit + bun test
pnpm --filter open-swe-cli run build
```
