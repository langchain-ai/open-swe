# Workspaces architecture

This document records the architecture, routing, settings hierarchy, migration behavior, and known gaps of Open SWE workspaces.

## Summary

A **workspace** in Open SWE may bind zero or more repositories, and a repository
may belong to multiple workspaces. The workspace absorbs the former environment (prompt, snapshot,
setup and update scripts, sandbox sizing, nightly refresh) and additionally owns the Slack
channels that route to it, its MCP connections, and workspace-scoped settings.
Inbound work is routed to a workspace by its thread, then its repository, then its Slack channel,
then the user's default, and otherwise to the `default` workspace, preserving the original
single-scope behavior. Every signed-in user sees every workspace, and every admin administers
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

- **Repositories.** Zero or more explicit `owner/name` bindings, shared across workspaces.
  Each binding has independent repository settings, including `may_start_threads`.
- **All GitHub App repositories.** `all_repositories` defaults to false. Enabling it grants
  sandbox and builder access to every repository reachable by this instance's GitHub App
  installation, including future additions. Explicit bindings remain available for routing
  and GitHub Actions trust configuration; enabling this flag does not create bindings.
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

An inherited default bound elsewhere is usable when also bound here or when this workspace
allows all installation repositories. Existing GitHub user and installation access checks
remain in place.

### Storage, run context, and API

Workspaces, and the repository and Slack-channel bindings that route to them, live in PostgreSQL,
not the LangGraph Store: a `workspace` table (one row per workspace, slug unique) plus
`workspace_repository` and `workspace_slack_channel`. The repository binding primary key is
`(repository_id, workspace_id)`, so sharing preserves each workspace's independent settings.
Slack channels still have exactly one owner. Settings and MCP connections stay exactly where this design places
them: in the LangGraph Store, keyed by workspace slug. `Workspace`, `WorkspaceCreate`, and
`WorkspaceUpdate` remain the domain and API shape that the dashboard, the agent tools, and routing
read and write; the tables are an implementation detail behind `WorkspaceStore`, swappable again
without touching a caller.

### Routing

Every thread records its workspace at creation and never changes it. Resolution for new work, in
order, and the first match wins:

1. An existing thread's recorded workspace. Follow-ups on issues, PRs, and Slack threads land here.
2. The repository named by the event or message, through its explicit bindings: `default`
   first if bound there, otherwise the lexicographically first workspace slug. This stable
   choice handles an event once, not once per workspace. The same order applies among
   bindings that explicitly enable `may_start_threads` for GitHub Actions federation.
3. The Slack channel's bound workspace.
4. The user's default workspace, a per-user preference.
5. The `default` workspace.

A `workspace:<slug>` tag on a message that opens a thread overrides steps 2 through 5, as the
`env:` tag does today, and `env:` keeps working as an alias. Instance policy decides what happens
to a GitHub event for a repository no workspace owns: route it to `default`, which is the
compatible upgrade behavior, or drop it, which a locked-down install should prefer.

### Repository access is independent of image setup

Changing repository bindings or `all_repositories` updates permissions without rebuilding the
image. Edits are accepted during an active refresh: a refresh writes only snapshot and refresh
state and an edit writes only the definition, so neither reverts the other. A refresh already
running keeps the access its builder started with until it finishes. Setup
scripts may install only tools, clone a selected subset, or preload no repositories at all. Runs can
clone other permitted repositories on demand.
`OPENSWE_WORKSPACE_REPOS` remains the explicit binding list for backward-compatible scripts;
app-wide access never expands it. Scripts that loop over this variable still choose to clone those
repositories on the next nightly/manual rebuild. Use **Rebuild image** when you want that, rather
than treating a permission edit as a rebuild. Removing permission does not erase existing checkouts
from snapshots: update the script and rebuild when filesystem isolation is required.

### Access

Every signed-in user may view and use every workspace, and every configured admin may administer
every workspace. Public threads remain visible to all signed-in users and private threads to their
owner and admins, as today. Role-based access per workspace is explicitly deferred.

### Dashboard

The Environments page becomes the Workspaces page, with repositories and Slack channels editable.
The composer picks the workspace first and the repository second: the repository list is the
workspace's explicit repositories, or all accessible repositories when app-wide access is on
(plus, for `default`, every unassigned one), and choosing a workspace
preselects its default repository. A repository named from outside — a link or the profile default —
still selects the workspace that owns it. The dashboard has no separate "project" notion: the
sidebar groups repositories under their deterministic default workspace route. Admin
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
validates, or one claiming a Slack channel another workspace owns — stays in the Store for the next
boot, the import does not count as complete, and GitHub deliveries for the repositories it names
are answered 503 so GitHub retries them rather than routing them to `default` or dropping them.

### Non-goals

- Per-workspace roles or membership. Everyone sees everything in this version.
- Per-workspace GitHub or Slack apps. One App and one Slack team per instance.
- Routing individual Slack messages within a channel to different workspaces.
- Per-workspace memory. The existing per-user memory stores are unchanged for now.
- A per-workspace MCP allowlist for externally triggered runs and public-safe prompts and outputs.

## Security and privacy

Partitioning settings by workspace removes the accidental path from a public repository's runs to
internal MCP connections, guidelines, and prompts, provided the public repositories live in a
workspace that has none of them. Repository-scoped tokens remain the default; installation-wide
sandbox access requires an explicit admin opt-in. Reviewer repository and permission restrictions
still narrow that scope. GitHub user/app checks and per-binding Actions federation grants are
unchanged. Leave app-wide access off for isolation-sensitive workspaces. Unassigned repositories are
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
- **Keep environments separate from workspaces.** Two objects with a one-to-one mapping would only
  add a join. Folding the environment in keeps a single admin surface.

## Open questions

- Should memory become per workspace, per user and workspace, or stay per user?
- Confirm the follow-up scope for public workspaces listed under non-goals.

Repository bindings reference `repository.id`, sharing a specific repository row rather than a name.

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
- **Unfinished dashboard pieces.** `groupSidebarThreadsByWorkspace` has no caller: the sidebar is
  flat until per-workspace grouping is designed. The review page's guidelines and toggles are
  per workspace, but the repository list above them is still every installed repository grouped by
  GitHub owner, which does not say which workspace each one belongs to.
