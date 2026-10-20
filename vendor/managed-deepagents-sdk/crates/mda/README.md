# `mda` CLI

Rust workspace member that builds the `mda` binary: project discovery, managed
compile, deploy, logs, delete, channel management, and local `mda dev`.

## LangSmith authentication

Commands that talk to LangSmith (`deploy`, `delete`, `logs`, and
credential-backed `mda dev` paths) resolve an API key in this order:

1. **`LANGSMITH_API_KEY` in the process environment or project `.env`** — treated
   as a durable credential the developer chose to keep. The shell wins when both
   set it (as for `LANGSMITH_ENDPOINT` and `LANGSMITH_WORKSPACE_ID`), and the CLI
   warns once when they differ.
2. **CLI-minted session PAT** — an unexpired personal access token created after
   browser device login, cached on this machine (see below).
3. **Interactive prompt** (TTY only) — paste a key (saved to `.env`) or press
   Enter to sign in with the browser.

### Browser sign-in (device flow)

When the developer presses Enter at the prompt, the CLI:

1. Runs the OAuth device authorization flow against the LangSmith platform API
   (`langsmith-cli` public client).
2. Mints a personal access token labeled `mda CLI` with a **2-hour** expiry
   (aligned with `npm login` session tokens).
3. Caches that PAT in the machine state directory and exports it to the process
   environment for the current run (so children such as `mda dev` can inherit
   it).

Browser-minted keys are **not** written to the project `.env`. That keeps short
sessions from leaving a dead credential in the repo after they expire; the next
CLI invocation falls through to a fresh sign-in (or reuses the cache while it is
still valid).

OAuth access and refresh tokens for the device flow are stored separately in
`builder-tokens.json` under the same state directory.

### Machine state directory

| Override / location | Path |
| --- | --- |
| `MDA_STATE_DIR` | `$MDA_STATE_DIR` |
| macOS / Linux (XDG) | `$XDG_STATE_HOME/mda` |
| macOS / Linux (default) | `~/.local/state/mda` |
| Windows | `%LOCALAPPDATA%\mda` |

| File | Contents |
| --- | --- |
| `builder-tokens.json` | OAuth access / refresh sessions, keyed by platform API URL |
| `cli-api-keys.json` | CLI-minted PATs (`key` + `expires_at`), keyed by platform API URL |
| `install-id` | Random UUID identifying this installation in product analytics |

Files are created mode `0600` and the directory mode `0700` on Unix when
possible.

### Longer sessions

For credentials that should outlive a 2-hour session (CI, shared project `.env`,
multi-day local work), create an API key under LangSmith **Settings → API Keys**
with the expiry you want, then paste it when the CLI prompts. That value is
saved to the project `.env` as `LANGSMITH_API_KEY`.

If this is not your machine, revoke any CLI-minted or pasted key under
**Settings → API Keys** when you are done.

### Stale keys in `.env`

Older CLI versions wrote browser-login PATs into `.env`. If authentication fails
with a key that is still present there, remove `LANGSMITH_API_KEY` from the
project `.env` (or replace it with a key you created in Settings) and run the
command again.

## Environment variables

CLI-facing knobs the `mda` binary reads (or injects into the staged build).
Variables exported in your shell can configure `mda` and are inherited by
`mda dev`. They are not automatically copied into remote deployments. Put model
provider keys and other deployment secrets in the project `.env` file.

For example, `model="openai:gpt-5.5"` uses `OPENAI_API_KEY` from the project
`.env`. Setting `model="langsmith:openai/gpt-5.5"` sends model calls through
LangSmith Gateway, which uses `OPENAI_API_KEY` stored in your LangSmith
workspace secrets instead. Your LangSmith API key authenticates with Gateway;
the workspace OpenAI key authenticates with OpenAI.

### Auth / LangSmith

See [LangSmith authentication](#langsmith-authentication) for key resolution
order and browser sign-in.

| Variable | Purpose |
| --- | --- |
| `LANGSMITH_API_KEY` | API key for deploy, logs, delete, and credential-backed `dev` |
| `LANGSMITH_WORKSPACE_ID` | Default workspace; overridable with `--workspace-id` on those commands |
| `LANGSMITH_ENDPOINT` | LangSmith API base URL (regional / self-hosted). CLI-side only for deploy/login; Host-reserved in the runtime (not a deployable secret). |
| `MDA_STATE_DIR` | Override for the machine state directory (cached PATs / OAuth tokens) |

### Compile / install

| Variable | Purpose |
| --- | --- |
| `MDA_LANGGRAPH_API_VERSION` | Opt into a non-default langgraph-api **version stem** (e.g. `0.13.0rc1`). Compile appends `-node22` / `-py{python_version}` into `_INTERNAL_docker_tag`. Blank falls back to the CLI default. |
| `MDA_PYTHON_VERSION` | Overrides `[tool.mda].python-version` for one build, as bare `major.minor` (e.g. `3.14`). Without either setting, selects the newest supported version allowed by `requires-python`, out of 3.11-3.14 (3.14 if no requirement is declared). A target that `requires-python` excludes is an error; patch-dependent compatibility produces a warning. |
| `MDA_INSTALL_CHANNEL` | Set by package launchers: `npm` or `pypi` |
| `MDA_BUNDLED_RUNTIME_PATH` | Set by package launchers: path to the bundled `managed-deepagents` runtime |
| `MDA_RUNTIME_VERSION` | Set by package launchers: registry package version reported by `mda --version` |

### Deploy / runtime injection

| Variable | Purpose |
| --- | --- |
| `MDA_PUBLIC_APP_URL` | Public app origin used when deriving CORS allowlist entries |
| `MDA_PUBLIC_API_URL` | Public Agent Server URL (deploy injects; local Events connectors need `https://…`) |
| `MDA_CORS_ORIGINS` | Extra comma-separated origins merged into `CORS_ALLOW_ORIGINS` |
| `MDA_DEPLOYMENT_NAME` | Injected so the runtime can name sandboxes for this deployment |
| `MDA_ASSISTANT_ID` | Injected graph / assistant id for channel ingress |
| `MDA_INGRESS_SECRET` | Shared secret for identity ingress header validation |

### Telemetry

| Variable | Purpose |
| --- | --- |
| `MDA_TELEMETRY_DISABLED` | Set to a truthy value to opt out. Telemetry is on by default. |
| `DO_NOT_TRACK` | Honoured the same way, for setting once across every tool that respects it |
| `MDA_POSTHOG_API_KEY` | Public PostHog project ingestion key. Baked in at release; a runtime value overrides it. |
| `MDA_POSTHOG_HOST` | Ingestion host override. Defaults to `https://us.i.posthog.com`. |

## Telemetry

The CLI sends one `command_completed` event per command, so we can see
which commands and flags people use and where they fail. It is on by default.
Opt out by exporting either variable in your shell:

```bash
export MDA_TELEMETRY_DISABLED=1
```

Opting out creates no installation id and sends nothing.

Delivery is best effort. The CLI never retries, never queues, and never lets a
failed send change a command's output, exit code, or side effects.

### What is sent

```json
{
  "event_id": "22222222-2222-4222-8222-222222222222",
  "install_id": "33333333-3333-4333-8333-333333333333",
  "invocation_id": "11111111-1111-4111-8111-111111111111",
  "event_type": "command_completed",
  "schema_version": "2026-08-20",
  "created_at": "1970-01-01T00:00:01.234Z",
  "cli_version": "0.5.3",
  "os": "linux",
  "install_channel": "npm",
  "invoker_detection_method": "interactive_terminal",
  "command_path": "channel init slack",
  "flags_present": ["name"],
  "outcome": "success",
  "user_id": null,
  "org_id": null,
  "workspace_id": null,
  "deployment_id": null,
  "deployment_name": null,
  "langsmith_host_url": "https://api.smith.langchain.com"
}
```

| Field | Meaning |
| --- | --- |
| `event_id` | Unique id for this event |
| `install_id` | Random UUID for this installation, minted on first use and stored in the machine state directory. CI reports a shared `ci` value |
| `invocation_id` | Unique id for one CLI run, shared by every event it produces |
| `event_type` | What happened. Currently only `command_completed` |
| `schema_version` | Version of this contract. Changes when a field is removed, renamed, or redefined |
| `created_at` | When the CLI built the event |
| `cli_version` | CLI version that ran |
| `os` | `linux`, `macos`, or `windows` |
| `install_channel` | `npm`, `pypi`, or `unknown` |
| `invoker_detection_method` | Signal observed about how the CLI was started (see below) |
| `command_path` | Normalized command, such as `channel init slack`. Never contains arguments or flags |
| `flags_present` | Names of the flags you passed, sorted. Never their values |
| `outcome` | `success`, `failure`, or `cancelled` |
| `error_code` | Reserved for a finer-grained code. Always `unclassified` today |
| `error_category` | Bounded reason for a `failure` (see below) |

Attribution fields are always present and null when unknown. `user_id` is the
verified LangSmith PAT owner, not proof of who operated the CLI; service keys do
not identify a human. `org_id` and `workspace_id` describe the authenticated target.
The CLI captures deployment names before execution (so deletion retains the name)
and looks up auth/deployment IDs afterward. Expired credentials and unavailable or
deleted deployments can leave IDs null. Lookups have one-second request timeouts,
never prompt for authentication, and are skipped on telemetry opt-out.

Runtime `run_completed` includes organization/workspace, API origin, and a canonical
Agent Auth `principal_id` from the current caller when known. Its optional auth
lookup runs once per process; failures stay unknown until restart. Inspection and
opt-out skip it. Existing `tenant_id` and `distinct_id` behavior is unchanged.

`langsmith_host_url` is the actual API origin without credentials, path, query, or
fragment. It enables production filtering downstream; the SDK retains all events.
Internal origins can differ from CLI origins. `environment=local/deployed` describes
execution location, not the LangSmith environment. `deploy --no-wait` success means
build submission, not deployment readiness.

### Failure categories

`error_category` is present only on a `failure`. The set is closed: a failure the
CLI does not recognize reports `unclassified` rather than anything free-form.

| Value | Meaning |
| --- | --- |
| `project` | The directory is not a Managed Deep Agent project, names both languages, or has an agent file we could not read |
| `compilation` | Compiling the project into a managed LangGraph app failed |
| `authentication` | No usable LangSmith credential, or LangSmith rejected the one given |
| `permission` | LangSmith refused the request |
| `remote` | LangSmith answered, but not with success |
| `network` | LangSmith could not be reached |
| `toolchain` | A child process the CLI runs, such as the dev server, failed |
| `deployment` | The deployment failed, was interrupted, or never finished in time |
| `user_input` | The command needed a name or a confirmation it could not ask for |
| `unclassified` | Anything else |

### Invoker signals

`invoker_detection_method` records the signal we observed, not a conclusion about
who you are:

| Value | Signal |
| --- | --- |
| `ci_environment` | A known CI variable is set, as detected by the `is_ci` crate |
| `interactive_terminal` | stdin and stderr are both terminals |
| `non_interactive_terminal` | Neither of the above |

### What is never sent

Flag values (except the documented deployment name), file or project paths, error
messages, credentials, email/profile data, arbitrary environment contents, and
project contents are never sent. Failures carry a bounded `error_category` instead
of the text shown to you.
