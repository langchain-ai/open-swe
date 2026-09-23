# open-swe CLI

`open-swe` starts a normal cloud Open SWE agent on a remote deployment and makes
the current directory on your laptop that agent's sandbox. The remote agent's
shell commands, file uploads and file downloads all execute here.

The deployment cannot dial your laptop, so the CLI long-polls the backend for
sandbox requests, runs them locally, and posts the results back.

> The agent runs commands **unsandboxed**, as you, in whatever directory you
> started it from. It can read, write and delete anything you can.

## Build

```sh
make cli            # -> cli/dist/open-swe
```

Requires [Bun](https://bun.com/docs/installation). The binary embeds its own
runtime, so the machine that runs it needs neither Bun nor Node. Copy it
anywhere on your `PATH`:

```sh
cp cli/dist/open-swe /usr/local/bin/
```

## Sign in

```sh
open-swe login --backend https://dev.open-swe.langchain.dev
```

This is the desktop app's sign-in: your browser opens the GitHub login the
dashboard uses, a loopback port catches the handoff, and PKCE S256 exchanges it
for the same session the desktop app stores. Requests then carry it as the
dashboard's own `osw_session` cookie, so the backend authenticates the CLI
exactly as it authenticates the app. The session and backend URL live in
`~/.open-swe/config.json` (mode 0600). `--backend` is optional once one is
stored, and `open-swe logout` deletes the file.

## Run

```sh
cd ~/code/my-project
open-swe run "add retries to the upload path"
```

What happens:

1. A sandbox bridge is registered for the current directory and starts serving
   the remote agent's requests. Bridge ids are remembered per directory in
   `~/.open-swe/bridges.json` and reused on the next run.
2. A thread is created on the deployment, with `origin` repo detected from
   `git remote get-url origin`, and its dashboard URL is printed.
3. Assistant text streams to the terminal; each tool call prints one dim line
   (`$ <command>` for shell tools).
4. When the run finishes, type a follow-up at the `>` prompt. The bridge keeps
   serving the whole time.

Ctrl-C once cancels the run, releases the bridge and exits. Ctrl-C twice exits
immediately.

Options:

| Flag | Meaning |
| --- | --- |
| `--thread <id>` | Continue an existing thread instead of creating one. Must be a thread this directory's bridge is bound to. |
| `--model <id>` | Agent model id. The backend only honors it together with `--effort`. |
| `--effort <name>` | Reasoning effort for `--model`. |

The prompt can also come from stdin: `open-swe run` alone reads one line.

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
