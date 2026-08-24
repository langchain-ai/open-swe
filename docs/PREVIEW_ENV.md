# Preview environment

A preview environment is a second instance of Open SWE, deployed from the `preview`
branch, that runs ahead of production. Set it up by following
[INSTALLATION.md](INSTALLATION.md) end to end a second time — its own GitHub App, Slack
app, Linear webhook, LangGraph deployment, and Vercel project.

This page covers only what is specific to running two instances side by side.

## The `preview` branch

| Branch | Contents | Updated by |
|---|---|---|
| `preview` | `main` + every open PR labeled `preview` | LangChain's deployment automation, which lives outside this repository and runs on demand |
| `prod` | Snapshot of `main` | `.github/workflows/promote_main_to_prod.yml`, daily at 08:00 UTC |

`preview` is force-pushed and rebuilt from scratch on every run — never commit to it
directly, and do not enable branch protection on it. Removing the `preview` label (or
merging/closing the PR) drops that PR on the next build. Each run also builds the
dashboard image and deploys the backend revision, so the branch and the running preview
environment move together.

### Testing an unmerged PR

Add the `preview` label, then ask a maintainer to run a preview deploy — labels no longer
trigger one on their own. Labeled PRs are merged in ascending PR number order; one that
fails to merge is skipped and the build continues with the next, so a single conflict
never blocks the queue. The run's summary lists what was merged and what was skipped.

**Only PRs whose `authorAssociation` is `OWNER` or `MEMBER` are merged.** GitHub computes
that server-side from organization membership, so it covers private members and cannot be
set by the PR author.

That gate is a security boundary: code merged into `preview` runs in the preview
deployment with preview credentials. Assembly happens outside this repository, from a
script this repository does not supply, and no PR's code is executed while the branch is
built.

## Keeping the two instances apart

Give preview a distinct `OPEN_SWE_MENTION_TAGS` (e.g. `@openswe-preview`). Linear
webhooks are per-workspace and a Slack workspace is shared, so the handle — not the
webhook — is what routes a mention to exactly one instance. Set each instance's
`EXTRA_INTERNAL_BOT_LOGINS` to the other's bot login so neither treats the other's
comments as untrusted external input.

Beyond the credentials that are obviously per-app, two variables are easy to
copy across by mistake:

- `TOKEN_ENCRYPTION_KEY` — generate a fresh one. A shared key lets either instance
  decrypt the other's stored user tokens; a separate key means users authenticate to
  preview separately.
- `REVIEWER_OUTCOMES_DATASET` — keeps preview's reviewer outcomes out of the production
  learning dataset.

Scope preview with its own `ALLOWED_GITHUB_ORGS` / `ALLOWED_GITHUB_REPOS` and install its
GitHub App only on the repositories it should touch.

## Dashboard

The dashboard reads `DASHBOARD_API_URL` — the backend it fronts — from its own
environment at request time, so one built image serves any deployment. Set it wherever
the dashboard runs (the pod environment on Kubernetes, the project settings on Vercel).
It has no default: a fallback would be production's backend, and preview would inherit it
and drive production's agents, threads, and sandboxes.

Requests stay same-origin — the server proxies `/dashboard/api/*` and `/webhooks/*` to
that backend — so `DASHBOARD_API_BASE_URL`, the GitHub App callback URL, and the
`DASHBOARD_ALLOWED_ORIGINS` entry must all point at the dashboard's own domain, not the
backend's. That allowlist is the backend's CSRF gate as well as its CORS config, so a
missing entry leaves the dashboard readable but unable to save.

Webhook senders get the dashboard's domain too: `/webhooks/*` is proxied for exactly this
reason, so each instance publishes one hostname rather than leaking its LangGraph URL.

## Verifying isolation

1. Mention `@openswe-preview` on a test PR — only the preview bot replies.
2. Mention `@openswe` on the same PR — only the production bot replies.
3. Label an open PR `preview` and confirm the next preview deploy lists it under **Merged**.
