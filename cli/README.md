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
web uses, a loopback port catches the handoff, and PKCE S256 exchanges it
for the same session the desktop app stores. Requests then carry it as the
web's own `osw_session` cookie. The session is stored in
`~/.open-swe/config.json` (mode 0600) under the backend that minted it, and
`oswe logout` forgets the current backend's session. `--backend` also makes
that backend the shared one, so it repoints the desktop app too.

### Check the credential

```sh
oswe auth status
```

Prints the backend, which credential is in use and where it came from, then
asks the server whether it accepts it: a session reports who it signs in as.
Exits 1 when there is no credential or the server rejects it.

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

With neither set, the backend is `backendUrl` in `~/.open-swe/config.json`,
the one file the CLI and the desktop app share, then `http://localhost:2024`
— the desktop app's own development default. Development builds of the
desktop app keep a separate profile and never change the shared file.

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
   `git remote get-url origin`, and its web URL is printed to stderr.
3. The agent works. Nothing it says or runs is printed; follow it on the
   web. If the event stream drops, for example while the backend
   restarts, the CLI reconnects with backoff; it gives up after 10 reconnects
   in a row that each lasted under a minute.
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

## Tool subcommands

Every MCP tool is accessible through `oswe tool NAME`, using the same session,
validation, authorization and implementation as the MCP server. Use the tool's
underscore name or its hyphenated spelling. Backend-provided tools appear
without rebuilding the CLI.

```sh
oswe tools
oswe tool list-threads --help
oswe tool list-threads --json '{"limit":5,"include_archived":true}'
printf '%s' '{"limit":5}' | oswe tool list_threads
oswe tool request-human-review --json '{"pr_url":"https://github.com/org/repo/pull/1","inline_summary":"Fix retry handling."}'
```

`oswe tools` prints the complete catalog, descriptions and JSON input schemas.
`oswe tool NAME --help` shows the selected tool's schema. Arguments are a JSON
object passed with `--json` or on stdin; omitting both uses `{}`. Results are
printed on stdout, errors on stderr, and tool failures exit 1. These commands
require a person's session, just like MCP; machine credentials cannot act for
someone. Tool discovery and help require access to the backend.

## MCP server

`oswe mcp` serves a [Model Context Protocol](https://modelcontextprotocol.io)
server on stdio, signed in with the same credential as the rest of the CLI:

```json
{ "mcpServers": { "oswe": { "command": "oswe", "args": ["mcp"] } } }
```

Every tool needs a person's session; API keys and CI tokens cannot use them.

`list_threads` lists your threads newest first with the web app sidebar's
filters: `repo` (owner/name) or `no_repo`, `include_archived`,
`include_automations`, `sort` (`created` or `updated`), plus `status`, `unread`,
`source`, `query`, `limit` and `offset`. Archived threads and automation runs
are left out unless asked for.

`create_session` creates a fresh cloud session from `prompt` and immediately
starts the agent. Set `start=false` to create an idle session instead. Optional
`repo` (owner/name), `workspace` (slug), and `visibility` (`public` or `private`)
use your saved web defaults and workspace routing when omitted. It returns
`thread_id` and the web app `url`; no local transcript or sandbox bridge is used.

`upload_session` moves a local coding session into a new Open SWE thread. It
takes `type` (`claude`), `transcript_path` (the session's JSONL, sent verbatim),
where the working directory was pushed — `repo` and `branch`, or `pr_url` — and
`visibility` (`workspace` by default, or `private`). Commit and push the whole
working directory first: the cloud agent sees only the pushed branch. No run
starts; continue the thread from the web app. A Claude Code transcript lives
at `~/.claude/projects/<cwd with non-alphanumerics as ->/$CLAUDE_CODE_SESSION_ID.jsonl`.

`request_human_review` posts a pull request's review card in its repository's
Slack review channel (or `channel`), with `inline_summary` as the card's
summary. It needs **Request human reviews in Slack** turned on for you on the
web's Feature Flags page. Asking again for a pull request you already
asked about replaces the open card's summary.

`dismiss_human_review_request` takes a pull request's open review request down,
as the card's Dismiss button does, with an optional `reason` shown on the card.

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
