# Preview environment

A preview environment is a second instance of Open SWE, deployed from the `preview`
branch, that runs ahead of production. Set it up by following
[INSTALLATION.md](INSTALLATION.md) end to end a second time — its own GitHub App, Slack
app, Linear webhook, LangGraph deployment, and Vercel project.

This page covers only what is specific to running two instances side by side.

## The `preview` branch

| Branch | Contents | Updated by |
|---|---|---|
| `preview` | `main` + every open PR labeled `preview` | `.github/workflows/build_preview_branch.yml`, on every push to `main` and on every label/push event for a labeled PR |
| `prod` | Snapshot of `main` | `.github/workflows/promote_main_to_prod.yml`, daily at 08:00 UTC |

`preview` is force-pushed and rebuilt from scratch on every run — never commit to it
directly, and do not enable branch protection on it. Removing the `preview` label (or
merging/closing the PR) drops that PR on the next build.

### Testing an unmerged PR

Add the `preview` label. Labeled PRs are merged in ascending PR number order; one that
fails to merge is skipped and the build continues with the next, so a single conflict
never blocks the queue. Each labeled PR gets one status comment (merged, conflicting, or
rejected), updated in place rather than re-posted.

**Only PRs whose `authorAssociation` is `OWNER` or `MEMBER` are merged.** GitHub computes
that server-side from organization membership, so it covers private members and cannot be
set by the PR author.

That gate is a security boundary: code merged into `preview` runs in the preview
deployment with preview credentials, and a merged `.github/workflows/` change would run
with repository secrets if it triggers on `preview`. The workflow itself only ever runs
`main`'s copy of `scripts/build_preview_branch.sh` and never executes PR code in CI.

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

## Vercel

Root directory `ui/`, production branch `preview`, and `DASHBOARD_API_URL` set to the
preview LangGraph deployment URL. Requests stay same-origin — the build turns that
variable into the platform rewrite for `/dashboard/api/*` — so `DASHBOARD_API_BASE_URL`,
the GitHub App callback URL, and the `DASHBOARD_ALLOWED_ORIGINS` entry must all point at
the preview Vercel domain. That allowlist is the backend's CSRF gate as well as its CORS
config, so a missing entry leaves the preview dashboard readable but unable to save.

`ui/vercel.json` limits git deployments to `main`, `prod`, and `preview`, so feature
branches build on neither project. Both projects use root directory `ui/` and therefore
read that same file — per-project deployment rules have to go in the Vercel dashboard
instead.

> The build **fails** if `DASHBOARD_API_URL` is unset on Vercel, rather than falling back
> to a default — a default would be production's backend, and preview would inherit it
> and drive production's agents, threads, and sandboxes from the preview dashboard.
>
> `DASHBOARD_API_URL` is read at build time. A change to it only takes effect on the next
> deployment, not on redeploy of an existing build.

## Verifying isolation

1. Mention `@openswe-preview` on a test PR — only the preview bot replies.
2. Mention `@openswe` on the same PR — only the production bot replies.
3. Label an open PR `preview` and confirm the workflow run summary lists it under **Merged**.
