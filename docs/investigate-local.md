# Investigate locally

Investigate has its own dashboard at `/investigate`. It enrolls public internal Slack channels only after a new `channel_created` or `channel_rename` event matches the configured prefix. Triage, incident-provider integrations, remediation, and fix PRs are outside this implementation.

## Start the dashboard

From the repository root, run:

```sh
make dev-ui
```

Open http://localhost:2024/investigate and sign in with the existing dashboard login. The Vite server runs on port 3000 and the backend on 2024. Investigate requires the existing observability allowlist; settings require an administrator.

For a populated local demo, leave `make dev-ui` running and start a separate terminal:

```sh
.venv/bin/python -m tests.investigations.preview
```

Open http://127.0.0.1:2025/investigate. The yellow banner identifies synthetic incident data. This test-only process uses the real Investigate API, event acceptance, Store serialization, coordinator, and channel worker with in-memory state and simulated Slack/model responses. It blocks external network requests and does not load environment files. Its session is confined to the preview app; production authentication is unchanged. Demo state resets when the process restarts.

Try opening the checkout investigation, following its evidence links, asking a question, pausing, resuming, completing, and reopening it. An explicit question can run while paused or completed and preserves that state. Settings changes use the same queued policy update path as production. The demo does not validate live model quality, Slack delivery, or Datadog/GitHub access.

## Connect a Slack workspace

1. Update and reinstall the Slack app using the generated manifest in **Admin → Slack integration** or the sample in [INSTALLATION.md](INSTALLATION.md). Investigate needs `channel_created`, `channel_rename`, `channel_archive`, `message.channels`, and `app_mention` events; public-channel access needs `channels:read`, `channels:join`, `channels:history`, `app_mentions:read`, and `chat:write`. Authorized Slack controls also use `users:read` and `users:read.email`. Set `SLACK_APP_ID` to the app ID on the app's Basic Information page; `scripts/create_apps.py --slack` writes it with the other Slack variables.
2. Point Slack's event request URL at the existing signed `/webhooks/slack` endpoint using a public HTTPS development tunnel or deployment. The installed bot must be permitted to join public channels. `conversations.replies` access can differ from history access; unavailable replies appear as coverage gaps.
3. In **Investigate → Settings**, configure a literal prefix such as `inc-`. The workspace comes from the installed bot token and the app from `SLACK_APP_ID`; both are shown read-only and cannot change after investigations are registered. Enabling checks the token's workspace identity and establishes minute recovery; the real creation/join/history trial below verifies installation capabilities.
4. Optionally select a supported model. Evidence access follows the installed credentials: repositories the GitHub App is installed on and, when the team Datadog connection is configured, Datadog services. All channel messages, including other bots' posts, are evidence; Open SWE's own output is excluded.
5. Enable Investigate, then create a new public internal channel matching the prefix, or rename an existing public channel into it. Post a symptom and relevant links. Verify that the bot joins and the investigation appears. No old-channel scan runs.
6. Every investigation posts channel-level messages in Slack: an introduction with the dashboard link, findings with impact, hypotheses, open questions, coverage gaps, and evidence links, answers to questions, and pause, resume, and completion notices. New messages trigger analysis after a 15-second debounce, and a pass posts only when its findings change; direct questions always post. The dashboard holds the full record for review.

Accepted events survive dispatch failures. A minute recovery task requeues only registered channels and accepted receipts; an event Slack never delivers cannot be recovered without a later matching rename. One channel worker is admitted at a time per workspace. Source checks and explicit pass limits bound work, and pause/complete controls are checked at tool and publication boundaries.

Content and channel checkpoints expire after 30 days from enrollment; receipts and delivery records are pruned after seven days once safe to remove. A minimal channel registration remains to prevent automatic re-enrollment. Protect raw LangGraph Store/thread APIs using the deployment's existing access controls; the dashboard's filtered projections do not secure those separate APIs.

The local implementation records an uncertain Slack send and avoids sending it again automatically. A responder must check the Slack thread; automated reconciliation of uncertain sends is deferred. Detailed delivery text expires after seven days, while delivery IDs/status remain until investigation expiry to prevent duplicate publication. Setup recovery uses minute retries. Slack transport and model failures are visible, and accepted questions stay queued through retryable model failures.

## Validation performed

- 224 targeted Python tests, including signed Slack ingress, authenticated API access, coordinator recovery, worker lifecycle, bounded tools, and the isolated preview.
- 11 dashboard/manifest tests, UI type checking, and scoped backend Ruff/type checks.
- Browser checks passed for pause, asking while paused, resume, complete, reopen, settings persistence, and evidence links; desktop, mobile, and dark-mode screenshots were inspected.
- Both graph entrypoints executed successfully through the running local LangGraph API using empty smoke-test inputs.
- A full dashboard production build passed during implementation; later UI changes passed compilation and type checks, with a concurrent build's final prerender blocked by the development server already occupying port 3000.

The demo uses deterministic evidence and model responses. A successful demo pass proves local wiring and lifecycle behavior; it does not establish a correct diagnosis of a real incident.
