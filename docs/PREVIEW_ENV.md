# Preview environment

A second, fully separate deployment of Open SWE that runs ahead of production, with
its own GitHub App, Slack app, LangGraph deployment, and Vercel project. Nothing is
shared with prod except the source repository.

## Branches

| Branch | Contents | Updated by |
|---|---|---|
| `main` | Merged work | PRs |
| `preview` | `main` + every open PR labeled `preview` (org members only) | `.github/workflows/build_preview_branch.yml`, on every push to `main` and on every label/push event for a labeled PR |
| `prod` | Snapshot of `main` | `.github/workflows/promote_main_to_prod.yml`, daily at 08:00 UTC |

`preview` is force-pushed and rebuilt from scratch on every run — never commit to it
directly. Removing the `preview` label (or merging/closing the PR) drops the PR from
the branch on the next build.

### Testing an unmerged PR on preview

Add the `preview` label. The build merges labeled PRs into `preview` in ascending PR
number order, and the LangGraph deployment picks it up. A PR that fails to merge is
skipped and the build continues with the next one — so one conflict never blocks the
rest of the queue.

Every labeled PR gets a single comment reporting its status (merged, conflicting, or
rejected for authorship). It is updated in place rather than re-posted, so repeated
rebuilds produce no extra notifications. Because merges run in PR number order, a
conflict is always reported against the lower-numbered PR already on the branch.

**Only PRs whose `authorAssociation` is `OWNER` or `MEMBER` are merged.** GitHub computes
that server-side from organization membership, so it covers private members and cannot be
set by the PR author. Outside collaborators and drive-by contributors are excluded.

This gate is a security boundary: code merged into `preview` runs in the preview
deployment with preview credentials, and a merged `.github/workflows/` change would run
with repository secrets if it triggers on `preview`. The workflow itself only ever runs
`main`'s copy of `scripts/build_preview_branch.sh` and never executes PR code in CI.

## What to provision

Each item below is created by hand once. Everything the application reads is an
environment variable, so no code changes are needed per environment.

### 1. GitHub App

Create a second app (e.g. "Open SWE Preview") following [INSTALLATION.md §3](INSTALLATION.md),
with its own webhook URL pointed at the preview LangGraph deployment. Install it only on
the repositories you want preview to touch — the app's installation list is what keeps
preview off production repositories.

Its bot login (e.g. `openswe-preview[bot]`) must be added to `EXTRA_INTERNAL_BOT_LOGINS`
on **both** deployments so neither treats the other's comments as untrusted external input.

### 2. Slack app

A second Slack app with its own bot token, signing secret, and OAuth client. Give it a
distinct handle so a mention routes to exactly one deployment, and invite it only to the
channels preview should serve.

### 3. Linear

Linear webhooks are per-workspace, so both deployments see the same comments. Isolation
comes from `OPEN_SWE_MENTION_TAGS` (below), not from the webhook. Use a separate Linear
API key so preview's comments are attributable.

### 4. LangGraph deployment

A new deployment tracking the `preview` branch, with the environment variables below.

### 5. Vercel project

A new project with root directory `ui/`, production branch `preview`, and
`VITE_DASHBOARD_API_BASE_URL` set to the preview LangGraph deployment URL. The UI calls
that URL directly (cross-origin with credentials), so add the preview Vercel domain to
`DASHBOARD_ALLOWED_ORIGINS` on the preview deployment.

> `ui/vercel.json` hardcodes the production LangGraph URL in a `/dashboard/api/:path*`
> rewrite. Vercel does not interpolate environment variables in `vercel.json`, so the
> preview project cannot override it. It is unused as long as
> `VITE_DASHBOARD_API_BASE_URL` is an absolute URL — which it must be for preview.

## Environment variables

### Must differ from prod

| Variable | Why |
|---|---|
| `OPEN_SWE_MENTION_TAGS` | Comma-separated mention handles this deployment answers to. Set to `@openswe-preview` on preview. Matching is boundary-aware, so prod's `@openswe` does **not** match `@openswe-preview`. Defaults to `@openswe,@open-swe,@openswe-dev`. |
| `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_OAUTH_PROVIDER_ID` | Preview's own GitHub App. |
| `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_BOT_USER_ID`, `SLACK_BOT_USERNAME` | Preview's own Slack app. |
| `LINEAR_API_KEY`, `LINEAR_WEBHOOK_SECRET` | Preview's own Linear integration. |
| `DASHBOARD_BASE_URL`, `DASHBOARD_API_BASE_URL`, `DASHBOARD_ALLOWED_ORIGINS` | Preview's Vercel domain and LangGraph URL. |
| `LANGGRAPH_URL` | Preview's own deployment URL. |
| `TOKEN_ENCRYPTION_KEY` | A fresh key. Sharing it would let either deployment decrypt the other's stored user tokens; a separate key means users authenticate to preview separately. |
| `RUN_COMPLETE_WEBHOOK_SECRET`, `X_SERVICE_AUTH_JWT_SECRET` | Independent secrets. |
| `EXTRA_INTERNAL_BOT_LOGINS` | The *other* deployment's bot login, on both sides. |
| `REVIEWER_OUTCOMES_DATASET` | Keeps preview's reviewer outcomes out of the production learning dataset. |

### Should differ

| Variable | Why |
|---|---|
| `ALLOWED_GITHUB_ORGS`, `PUBLIC_REPO_ORG_GATE` | Narrow preview to the repositories it should serve. |
| `DEFAULT_REPO_OWNER`, `DEFAULT_REPO_NAME`, `SLACK_REPO_OWNER`, `SLACK_REPO_NAME` | Point preview at a test repository. |
| `LANGCHAIN_PROJECT` / LangSmith tracing project | Keeps preview traces separate. |
| `OBSERVABILITY_AUTHORIZED_EMAILS` | Usually a smaller list on preview. |

### Safe to share

LLM provider keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `GROQ_API_KEY`),
`DEFAULT_SANDBOX_SNAPSHOT_ID`, `REPO_SNAPSHOT_BASE_IMAGE`, and the timeout/limit tuning
variables.

## Verifying isolation

1. Mention `@openswe-preview` on a test PR — only the preview bot should reply.
2. Mention `@openswe` on the same PR — only the production bot should reply.
3. Label an open PR `preview`, then check the workflow run summary lists it under **Merged**.
4. Open the preview Vercel URL and confirm the dashboard loads and login succeeds.
