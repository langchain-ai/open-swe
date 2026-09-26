# Workspaces architecture

This document records the architecture, routing, settings hierarchy, migration behavior, and known gaps of Open SWE workspaces.

## Summary

A **workspace** in Open SWE lists zero or more **preferred repositories**, and a repository is
preferred by at most one workspace. Preference decides where a repository's events run and what
the workspace's sandbox image preloads; it does not limit access: every workspace's threads can
use every repository the GitHub App installation can reach. The workspace absorbs the former
environment (prompt, snapshot, setup and update scripts, sandbox sizing, nightly refresh) and
additionally owns the Slack channels that route to it, its MCP connections, and workspace-scoped
settings. Inbound work is routed to a workspace by its thread, then a tag, then its Slack channel,
then its repository's preferred workspace, then the user's default, and otherwise to the
`default` workspace. Every signed-in user sees every workspace, and every admin administers
every workspace; workspace roles remain future work.

## Motivation

The OSS team wants Open SWE to handle issues on public repositories. Before workspaces, one
deployment was one scope: the same settings, MCP connections such as Datadog, and
installation-wide GitHub access served every run. Trigger gates keep outsiders from starting runs, but extending the same scope to
public repositories means public content meets internal access. Deploying a second instance
avoids that but is heavyweight for what will be a common split.

Two concepts already point at the answer. Environments carry a prompt, a snapshot, and a list of
repositories, and are selected per thread. MCP connections and thread visibility are already
called "workspace" scoped. The workspace is a first-class object and folds the
environment into it, since the two would otherwise map one to one.

## Design

### The workspace record

A workspace has an immutable slug and a display name, plus:

- **Preferred repositories.** Zero or more `owner/name` entries. A repository is preferred by at
  most one workspace; saving a workspace that claims a repository another workspace prefers fails
  with a conflict, so moving a preference is an explicit edit. A preferred repository's GitHub
  events, Linear issues, and automations run here, and its binding carries repository settings
  such as `may_start_threads`. Preferring a repository is not what grants access to it.
- **Environment fields**, moved from the environment record unchanged: prompt, base snapshot,
  setup and update scripts, sandbox resources, create parameters, captured snapshot state, and
  the refresh schedule. Snapshot names keep their current form so existing snapshots stay valid.
- **Slack channels.** Zero or more channel ids whose messages route here. A channel routes to one
  workspace.
- **MCP connections** of its own, layered over the instance-wide set that existed before
  workspaces (see "Settings tiers").
- **Settings**, the former team settings: model defaults, review toggles, organization
  guidelines, gateway and Fable toggles, default repository. These are tiered; see
  "Settings tiers" below.

Instance-level configuration stays in the environment: the GitHub App and its installation, the
Slack app, model provider keys, sandbox provider and sandbox credentials, `CONFIGURED_ADMINS`, the
sign-in allowlist, and the base snapshot fallback.

### Settings tiers

Settings resolve from the least to the most specific tier, each overriding the one before:

1. **Instance.** The settings record as it existed before workspaces (then "team settings"), kept under its
   original Store key so an upgrade needs no migration. Admins edit it on the Admin page.
2. **Workspace.** A sparse record of overrides per workspace slug. A field that is unset inherits
   the instance value, so a new workspace behaves exactly like the instance until an admin
   changes something. `GET /dashboard/api/workspaces/{slug}/settings` returns the effective
   values and the overrides separately; `PUT` replaces the overrides, and a `null` field means
   "inherit".
3. **User.** The existing profile fields (model and effort, subagent model, default repository,
   adaptive routing, draft-PR review) override the workspace's effective settings for that user's
   runs.
4. **Thread.** A run's `configurable` (`agent_model_id`, `agent_effort`, `repo`) overrides all
   of the above for that thread.

MCP connections follow the same shape without a thread tier: **instance** connections
(`/dashboard/api/mcps`) are loaded for every run, the **workspace** connections are added, then
the **user's** personal connections. A later tier's connection replaces a same-named one from
the tier before, which is how a workspace or a user swaps in different credentials for a shared
server. The connections configured before workspaces existed are the instance tier: they
keep applying to every workspace, as they always did.

A workspace may use any accessible repository as its default repository, including one another
workspace prefers. Existing GitHub user and installation access checks remain in place.

### Storage, run context, and API

Workspaces, and the repository and Slack-channel bindings that route to them, live in PostgreSQL,
not the LangGraph Store: a `workspace` table (one row per workspace, slug unique) plus
`workspace_repository` and `workspace_slack_channel`, which key on the bound resource
(`repository_id`, referencing the `repository` table, and `channel_id`), so the database itself
enforces that a repository is preferred by, and a Slack channel bound to, at most one workspace. Settings and MCP connections stay exactly where this design places
them: in the LangGraph Store, keyed by workspace slug. `Workspace`, `WorkspaceCreate`, and
`WorkspaceUpdate` remain the domain and API shape that the dashboard, the agent tools, and routing
read and write; the tables are an implementation detail behind `WorkspaceStore`, swappable again
without touching a caller.

### Routing

Every thread records its workspace at creation and never changes it. Resolution for new work, in
order, and the first match wins:

1. An existing thread's recorded workspace. Follow-ups on issues, PRs, Linear issues, and Slack
   threads land here; none of them recompute the workspace from the repository.
2. A `workspace:<slug>` tag on the message that opens the thread (`env:` keeps working as an alias).
3. The Slack channel's bound workspace. A message in a workspace's channel runs there even when it
   names a repository another workspace prefers, since every workspace can use every repository.
4. The repository's preferred workspace. This is how GitHub events, Linear issues, automations, and
   GitHub Actions federation (through the binding's `may_start_threads`) pick a workspace.
5. The user's default workspace, a per-user preference.
6. The `default` workspace.
 Instance policy decides what happens
to a GitHub event for a repository no workspace owns: route it to `default`, which is the
compatible upgrade behavior, or drop it, which a locked-down install should prefer.

### GitHub access and the sandbox image

Every thread's sandbox gets a token for the whole GitHub App installation, so a thread in any
workspace can clone and push to any repository the App can reach. The one exception is a thread
started by a GitHub event or an issue automation on a **public** repository: it records that
repository in its thread metadata when it is created, server-side and never from run
configuration, and its token is narrowed to that repository alone, including on every proxy
refresh. Threads that members start from Slack, the dashboard, or Linear get the full token.

A workspace's preferred repositories are what its image preloads: the setup and update scripts
receive them in `OPENSWE_WORKSPACE_REPOS`, and changing them rebuilds the image. Any other
repository is cloned on demand by the run that needs it. A refresh writes only snapshot and refresh
state and an edit writes only the definition, so neither reverts the other.

### Access

Every signed-in user may view and use every workspace, and every configured admin may administer
every workspace. Public threads remain visible to all signed-in users and private threads to their
owner and admins, as today. Role-based access per workspace is explicitly deferred.

### Dashboard

The Environments page becomes the Workspaces page, with repositories and Slack channels editable.
The composer picks the workspace first and the repository second: every workspace lists every
accessible repository, and choosing a workspace preselects its default repository. A repository
named from outside — a link or the profile default — selects the workspace that prefers it. The
dashboard has no separate "project" notion: the sidebar groups each thread under its own
workspace, then its repository, so threads from two workspaces on one repository appear under
both. Admin
settings and MCP connections gain a workspace selector.

### Migration

Existing environment records become workspace records with the same slugs. The current team
settings and MCP connections become the `default` workspace's. Existing threads without a
workspace read as `default`. The `environment` key in thread metadata and run configuration is
read as an alias for `workspace` so in-flight threads keep working.

Environment records that predate PostgreSQL storage still live in the LangGraph Store, under the
`["workspaces"]` namespace and the legacy `["environments"]` namespace before that. At startup,
right after migrations run, `import_store_records()` reads both namespaces once, writes any slug
that has no PostgreSQL row yet, and deletes the Store record once it has been dealt with — so this
runs exactly once per record, resurrects nothing an admin has since deleted, and a later release
can drop the whole path. A Store outage at that moment is logged and simply retried on the next
boot; nothing blocks startup on it. A record the import cannot bring over — one that no longer
validates, or one claiming a repository or Slack channel another workspace owns — stays in the Store for the next
boot, the import does not count as complete, and GitHub deliveries for the repositories it names
are answered 503 so GitHub retries them rather than routing them to `default` or dropping them.

### Non-goals

- Per-workspace roles or membership. Everyone sees everything in this version.
- Per-workspace GitHub or Slack apps. One App and one Slack team per instance.
- Routing individual Slack messages within a channel to different workspaces.
- Per-workspace memory. The existing per-user memory stores are unchanged for now.
- A per-workspace MCP allowlist for externally triggered runs and public-safe prompts and outputs.

## Security and privacy

Partitioning settings by workspace removes the accidental path from a public repository's events to
internal MCP connections, guidelines, and prompts, provided the public repositories are preferred
by a workspace that has none of them. GitHub access is not partitioned: every sandbox token covers
the whole installation, so the token is not what isolates a workspace. Threads started by GitHub
events or automations on public repositories, where outsiders' content is most likely, get a token
for that repository only; the reviewer, analyzer, and review scout keep their own single-repository
tokens. A member-started thread can reach every installation repository, including ones the member
cannot access on GitHub themselves. Unassigned repositories are
routed by an explicit instance policy rather than silently defaulting, so a locked-down install can
drop them. No new credentials are introduced, and MCP connection secrets stay encrypted under the
workspace they belong to.

## Alternatives

- **Deploy a second instance for OSS.** Works today with no code, but doubles operations for a
  split that many teams will want, and gives no shared dashboard or identity.
- **Multiple GitHub Apps, one per workspace.** Couples sign-in and webhook secrets to
  workspaces and requires separate credentials. Shared bindings work with one installation.
- **Workspaces bound to installations rather than repositories.** Simpler routing, but one
  GitHub organization then cannot host more than one workspace, which is the common case here.
- **Repository access scoped per workspace, with repositories shared between workspaces.** Kept
  the token narrow, but made every repository a workspace wanted to touch an access decision and
  a routing decision at once, and left shared repositories' events with no clear owner.
- **Keep environments separate from workspaces.** Two objects with a one-to-one mapping would only
  add a join. Folding the environment in keeps a single admin surface.

## Open questions

- Should memory become per workspace, per user and workspace, or stay per user?
- Confirm the follow-up scope for public workspaces listed under non-goals.

## Follow-ups

Known gaps this design leaves open, so they survive outside the pull requests that built it.

- **A renamed or transferred repository keeps its old binding.** An inbound webhook is matched to a
  `repository` row by `repository.key` (lowercased `owner/name`), and nothing updates an existing
  row's name, so the first event for the new name inserts a second row while the workspace binding
  stays on the old one. The practical remedy today is to re-save the workspace's repository list
  with the new name, which rebinds it; under
  `OPEN_SWE_UNASSIGNED_REPO_WORKSPACE=ignore` the deliveries in between are answered 200 ignored
  and never retried. A reconciliation job that follows GitHub's `repository.renamed` event would
  close it.
- **A corrupt workspace row is skipped rather than repaired.** A record an older release wrote that
  no longer validates is left out of the listing and reads as missing, logged at error. Nothing
  reports it to an admin, and there is no way to fix it from the dashboard.
- **Concurrent edits to one workspace overwrite each other.** `apply_update` reads a record, applies
  the patch, and writes the whole row back, so the last save wins; two admins editing the same
  workspace need optimistic concurrency on `updated_at` to notice.
- **The public-workspace lockdown.** A per-workspace MCP allowlist for
  externally triggered runs, and public-safe prompts and outputs are what a genuinely public
  workspace needs beyond partitioning. That is the next proposal, and it depends on this one.
- **The Linear default repository reads the default workspace.** A Linear issue with no repository
  in it falls back to `default`'s configured repository rather than the resolved workspace's, unlike
  the Slack path, which scopes a defaulted repository to the workspace that won.
- **Unfinished dashboard pieces.** The review page's guidelines and toggles are
  per workspace, but the repository list above them is still every installed repository grouped by
  GitHub owner, which does not say which workspace each one belongs to.
