# Robin for iOS

An Expo (React Native) client for Robin: an inbox of your agents, their transcripts, and the changes they made.

## Run it

Requirements: Xcode 26 or later with an iOS simulator, CocoaPods, and the workspace's Node and pnpm.
From the `open-swe` directory:

```bash
pnpm install
```

```bash
cd mobile && pnpm ios
```

`pnpm ios` runs `expo run:ios`, which generates the native project into `ios/` (ignored by git), installs pods, builds a development build, launches it in a simulator, and starts Metro.
Pass `--device` to pick a simulator or a connected iPhone.
After the first build, `pnpm start` is enough for JavaScript-only changes.

The app needs a development build rather than Expo Go, because it includes its own native module (`modules/open-swe-auth`) and SwiftUI controls from `@expo/ui`.
Only iOS is supported.

At launch you can either:

- **Continue in Browser.**
  Enter your Robin backend (for example `robin.example.com`) and sign in.
  For a backend on this machine running under `langgraph dev` (`http://127.0.0.1:2024`), the app signs in through `dev-login` as the `gh` CLI's user, the same way the local web dashboard does, so no LangSmith login is needed.
- **Explore a Demo Workspace.**
  This uses in-memory sample agents and needs no backend.

## What it does

| Screen | What it shows |
| --- | --- |
| Inbox | Category cards (All Agents, Needs Attention, Working, In Review) and your workspaces (repositories) |
| Agent list | Agents grouped by category or repo, with search, swipe to archive or pin, and a context menu |
| Customize | Group by, filters (repo, status, git, source) and row properties (changes, branch status, runtime, last updated) |
| New agent | A prompt, the repository (or none) and visibility, started the way the dashboard starts one |
| Agent | The live transcript: your messages, grouped tool activity, the to-do card, agent replies, a Changes card and a follow-up composer (queued behind the run while the agent works) |
| Changes | The pull request header, check status, and per-file unified diffs |

Categories come from each thread's summary:

- **Working**: the agent is running.
- **In Review**: the agent has an open or draft pull request.
- **Needs Attention**: the agent is unread, failed, was stopped, or has an attention reason.
- **Older**: everything else.

Archiving an agent resolves its thread.

## How it talks to the backend

The app calls the dashboard API at `<backend>/dashboard/api` and needs no backend changes:

- **Sign-in** uses the desktop PKCE handoff that the CLI and desktop app use.
  The app opens `/auth/login?desktop=1&desktop_handoff=<challenge>&desktop_port=<port>` in an authentication session and listens on `127.0.0.1:<port>` for the `/callback?code=…` redirect.
  It then exchanges the code and verifier at `/auth/desktop/exchange` for a session.
  The loopback listener lives in `modules/open-swe-auth`, as does the `dev-login` request, which has to read the session cookie from a redirect that `fetch` would follow.
- **Sessions** are stored in the Keychain (this device only, through `expo-secure-store`) and sent as the `osw_session` cookie with the backend's own `Origin`, like the CLI.
- **Transcripts** come from the thread's event log, as on the dashboard: `GET /threads/{id}/transcript` for the snapshot, then `GET /threads/{id}/transcript/events` (server-sent events) applied live, reopened after the last applied version if the stream drops.
  Model-only context (`<dynamic-context>`, silent system senders) is hidden and `<input-message>` envelopes are unwrapped.
  Threads that predate the log fall back to polling `/threads/{id}/state`.
- **Reads:** `/me`, `/me/preferences`, `/repos`, `/threads/page` (refreshed every 20 seconds while the app is open), `/threads/pinned`, `/threads/{id}`, `/threads/{id}/branch-diff` and `/threads/{id}/pull-request-status`.
- **Writes:** `PATCH /threads/{id}` (rename), `POST|DELETE /threads/{id}/pin`, `POST /threads/{id}/resolve` (archive), and `POST /threads/{id}/commands` with `run.start`.
  That command both sends follow-ups and starts new agents: the first `run.start` on a new, client-minted thread id creates the thread, as in the dashboard.

API keys can't be used here: they are machine credentials, and the person-scoped endpoints require a session.

## Layout

```text
src/
  app/          Expo Router screens: inbox, agent list, thread, changes, details, customize, new agent, sign-in
  core/         API client, models, transcript and diff parsing, sign-in, Keychain, demo data
  design/       theme tokens, shared components, markdown rendering
  features/     agent rows, transcript items, thread actions and the live thread follower
  navigation/   route helpers
  state/        AppModel (auth), AgentStore (data), list preferences
modules/
  open-swe-auth/  native module for the sign-in loopback listener and dev-login
```

Navigation chrome is native: Expo Router's native stack with large titles, toolbar buttons and menus, the bottom search bar, context menus and form sheets.
Pickers, toggles and menus inside screens are SwiftUI controls from `@expo/ui`.

Colors come from the dashboard's design system: `src/design/theme.ts` holds the Macaw semantic tokens (`@langchain/macaw-tokens`) the web and desktop app use, with their light and dark values.
The app follows the system appearance, as the dashboard does.

Run the type check from `mobile/`, and the workspace's lint and format checks from the `open-swe` root:

```bash
pnpm typecheck
```

```bash
pnpm run lint && pnpm run format:check
```
