# Robin CLI

`robin` starts a Robin thread on a remote deployment from the command line and waits for it to finish.
The agent works in the thread's cloud sandbox, on the GitHub repository the current directory's `origin` names; nothing runs on the machine running `robin`.

## Install

On Apple Silicon Macs running macOS Ventura or later, you can install with Homebrew.
It installs the desktop app and links the legacy `oswe` command onto your `PATH`; the CLI updates with the app.

```sh
brew install --cask langchain-ai/tap/open-swe-desktop
```

If you installed the cask before it linked `oswe`, run `brew reinstall --cask open-swe-desktop` once to get the link (a plain `brew upgrade` skips auto-updating casks).
Use `oswe` in place of `robin` in the examples below until the cask also links `robin`.

For Intel Macs, Linux, or just the CLI, use a standalone download.
Every desktop release, stable and nightly, ships `robin` and the compatible `oswe` command inside the app at `Robin.app/Contents/Resources/bin/` and as standalone downloads for `darwin-arm64`, `darwin-x64`, `linux-arm64` and `linux-x64` with a `robin-SHA256SUMS` file.
The macOS binaries are signed and notarized.

```sh
curl -fsSL https://github.com/langchain-ai/open-swe/releases/latest/download/robin-darwin-arm64.tar.gz | tar -xz
sudo mv robin /usr/local/bin/
```

`robin --version` prints the desktop release it came from.
Legacy `oswe-*` downloads remain available; both commands use the same configuration, credentials, and thread history.

## Build

```sh
make cli            # -> cli/dist/robin and cli/dist/oswe
```

Requires [Bun](https://bun.com/docs/installation).
The binary embeds its own runtime, so the machine that runs it needs neither Bun nor Node.
Copy it anywhere on your `PATH`:

```sh
cp cli/dist/robin /usr/local/bin/
```

## Authenticate

The CLI uses the first credential it finds:

1. **`OPEN_SWE_API_KEY`** — a LangSmith API key from the LangSmith workspace Robin signs people in to (keys from any other workspace are refused).
   A personal access token (`lsv2_pt_…`) acts as its person, whose Robin login must be live; a service key (`lsv2_sk_…`) with `workspaces:manage` (a Workspace Admin key) acts as the workspace's bot.
1. **A person's session** — `OPEN_SWE_SESSION`, or the one `robin login` stored.

A service key is a machine: its threads are always `system` threads.
A person's threads are always private.

### Sign in as a person

```sh
robin login --backend https://dev.open-swe.langchain.dev
```

This is the desktop app's sign-in: your browser opens the GitHub login the dashboard uses, a loopback port catches the handoff, and PKCE S256 exchanges it for the same session the desktop app stores.
Requests then carry it as the dashboard's own `osw_session` cookie.
The session is stored in `~/.open-swe/config.json` (mode 0600) under the backend that minted it, and `robin logout` forgets the current backend's session.
`--backend` also makes that backend the shared one, so it repoints the desktop app too.

### Check the credential

```sh
robin auth status
```

Prints the backend, which credential is in use and where it came from, then asks the server whether it accepts it: a session reports who it signs in as.
Exits 1 when there is no credential or the server rejects it.

### Run in GitHub Actions

Use a Workspace Admin service key stored as a repository secret:

```yaml
permissions:
  contents: read
steps:
  - uses: actions/checkout@v4
  - run: robin run "fix the flaky upload test and open a pull request"
    env:
      OPEN_SWE_BACKEND_URL: https://robin.example.com
      OPEN_SWE_API_KEY: ${{ secrets.ROBIN_SERVICE_KEY }}
```

### Backend

| Variable | Effect |
|---|---|
| `OPEN_SWE_BACKEND_URL` | the backend to call |
| `OPEN_SWE_DESKTOP_URL` | the same, checked second |

With neither set, the backend is `backendUrl` in `~/.open-swe/config.json`, the one file the CLI and the desktop app share, then `http://localhost:2024` — the desktop app's own development default.
Development builds of the desktop app keep a separate profile and never change the shared file.

The binary never reads a `.env`.
It is compiled with `--no-compile-autoload-dotenv`, because it runs inside your repository and a `.env` there would otherwise enter its environment.

## Run

```sh
cd ~/code/my-project
robin run "add retries to the upload path"
```

What happens:

1. A thread is created on the deployment for the GitHub repository `git remote get-url origin` names, and its dashboard URL is printed to stdout, the only thing the CLI prints there.
   Outside a checkout with a GitHub `origin`, the thread starts without a repository.
   The agent clones the repository in its cloud sandbox, so commits you have not pushed are not there.
1. The agent works.
   Nothing it says or runs is printed; follow it on the dashboard.
   The CLI asks the backend about once a second whether the thread has anything left to run.
   If the backend can't answer, for example while it restarts, the CLI retries with backoff; it gives up after 10 retries in a row.
1. Once the thread has nothing left to run, the CLI exits: 0 when the agent's last run completed, 1 when it failed, was cancelled, or stopped to wait for an answer, in which case it prints the run's status and the thread's URL to stderr.

The agent may hand parts of the work to task workers.
While any worker is still running, the CLI notes each worker's dashboard link and state on stderr as it changes, and waits through the runs that workers' reports wake the agent for.

Ctrl-C once cancels the run and every worker still running, and exits with 130, as an interrupted command does.
Ctrl-C twice exits immediately, also with 130.

Options:

| Flag | Meaning |
| --- | --- |
| `--thread <id>` | Continue an existing thread you can post to. |
| `--model <id>` | Agent model id, with `--effort`; the backend refuses a model or effort it does not support. |
| `--effort <name>` | Reasoning effort for `--model`. |

Piped input is attached below the prompt inside `<stdin>` tags, or is the whole prompt when no arguments are given.
Stdin is only read when it is a pipe or a redirected file, so a CI runner's open stdin never blocks a run.

## Development

```sh
pnpm install --filter open-swe-cli   # from the repository root
pnpm --filter open-swe-cli run check # tsc --noEmit + bun test
pnpm --filter open-swe-cli run build
```
