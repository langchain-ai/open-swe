# OEP-0003: Workspaces

- **Authors:** Mukil Loganathan (`@langchain-infra`)
- **Status:** Draft
- **Created:** 2026-09-13
- **Discussion:** https://github.com/langchain-ai/open-swe/pulls?q=is%3Apr+OEP-0003
- **Supersedes:** None

## Summary

Add a **workspace** to Open SWE. A workspace owns one or more repositories, and a repository
belongs to exactly one workspace. The workspace absorbs today's environment (prompt, snapshot,
setup and update scripts, sandbox sizing, nightly refresh) and additionally owns the Slack
channels that route to it, its MCP connections, and the settings that are instance-wide today.
Inbound work is routed to a workspace by its thread, then its repository, then its Slack channel,
then the user's default, and otherwise to the `default` workspace, which is what the current
single-scope install becomes. In this version every signed-in user sees every workspace and every
admin administers every workspace; roles come later.

## Motivation

The OSS team wants Open SWE to handle issues on public repositories. Today one deployment is one
scope: the same settings, MCP connections such as Datadog, and installation-wide GitHub access
serve every run. Trigger gates keep outsiders from starting runs, but extending the same scope to
public repositories means public content meets internal access. Deploying a second instance
avoids that but is heavyweight for what will be a common split.

Two concepts already point at the answer. Environments carry a prompt, a snapshot, and a list of
repositories, and are selected per thread. MCP connections and thread visibility are already
called "workspace" scoped. This proposal makes the workspace a real object and folds the
environment into it, since the two would otherwise map one to one.

## Proposal

### The workspace record

A workspace has an immutable slug and a display name, plus:

- **Repositories.** One or more `owner/name` entries. A repository may appear in exactly one
  workspace; saving a workspace that claims a repository another workspace owns fails. The
  `default` workspace may list no repositories, because it also receives unassigned work.
- **Environment fields**, moved from the environment record unchanged: prompt, base snapshot,
  setup and update scripts, sandbox resources, create parameters, captured snapshot state, and
  the refresh schedule. Snapshot names keep their current form so existing snapshots stay valid.
- **Slack channels.** Zero or more channel ids whose messages route here. A channel routes to one
  workspace.
- **MCP connections**, moved from the instance-wide set into the workspace.
- **Settings** that are team settings today: model defaults, review toggles, organization
  guidelines, gateway and Fable toggles, default repository. Each workspace has its own record.
  A new workspace starts from the hardcoded defaults; inheriting from `default` is a later change.

Instance-level configuration stays in the environment: the GitHub App and its installation, the
Slack app, model provider keys, sandbox provider and sandbox credentials, `CONFIGURED_ADMINS`, the
sign-in allowlist, and the base snapshot fallback.

### Routing

Every thread records its workspace at creation and never changes it. Resolution for new work, in
order, and the first match wins:

1. An existing thread's recorded workspace. Follow-ups on issues, PRs, and Slack threads land here.
2. The repository named by the event or the message, through its owning workspace.
3. The Slack channel's bound workspace.
4. The user's default workspace, a per-user preference.
5. The `default` workspace.

A `workspace:<slug>` tag on a message that opens a thread overrides steps 2 through 5, as the
`env:` tag does today, and `env:` keeps working as an alias. Instance policy decides what happens
to a GitHub event for a repository no workspace owns: route it to `default`, which is the
compatible upgrade behavior, or drop it, which a locked-down install should prefer.

### Access

Every signed-in user may view and use every workspace, and every configured admin may administer
every workspace. Public threads remain visible to all signed-in users and private threads to their
owner and admins, as today. Role-based access per workspace is explicitly deferred.

### Dashboard

The Environments page becomes the Workspaces page, with repositories and Slack channels editable.
The composer's environment picker becomes a workspace picker that preselects the workspace owning
the chosen repository. The sidebar groups threads by workspace, and grouping by repository remains
available within a workspace, since every repository sits inside exactly one workspace. Admin
settings and MCP connections gain a workspace selector.

### Migration

Existing environment records become workspace records with the same slugs. The current team
settings and MCP connections become the `default` workspace's. Existing threads without a
workspace read as `default`. The `environment` key in thread metadata and run configuration is
read as an alias for `workspace` so in-flight threads keep working.

### Non-goals

- Per-workspace roles or membership. Everyone sees everything in this version.
- Per-workspace GitHub or Slack apps. One App and one Slack team per instance.
- Routing individual Slack messages within a channel to different workspaces.
- Settings inheritance from `default`. Likely later.
- Per-workspace memory. The existing per-user memory stores are unchanged for now.
- The controls a public workspace needs beyond partitioning: repository-scoped tokens, a per-workspace MCP allowlist for externally triggered runs, and public-safe prompts and outputs. Those are the next proposal and depend on this one.

## Security and privacy

Partitioning settings by workspace removes the accidental path from a public repository's runs to
internal MCP connections, guidelines, and prompts, provided the public repositories live in a
workspace that has none of them. It does not, by itself, scope the GitHub installation token or
mark public content untrusted; see the last non-goal. Repository ownership is validated on save so
routing is deterministic and a PR is never handled by two workspaces. Unassigned repositories are
routed by an explicit instance policy rather than silently defaulting, so a locked-down install can
drop them. No new credentials are introduced, and MCP connection secrets stay encrypted under the
workspace they belong to.

## Alternatives

- **Deploy a second instance for OSS.** Works today with no code, but doubles operations for a
  split that many teams will want, and gives no shared dashboard or identity.
- **Multiple GitHub Apps, one per workspace.** Would let a repository appear in several
  workspaces, but couples sign-in and webhook secrets to workspaces and makes admins manage app
  credentials. One App with repository ownership is simpler and matches how Devin, CodeRabbit, and
  Copilot partition repositories under a single installation.
- **Workspaces bound to installations rather than repositories.** Simpler routing, but one
  GitHub organization then cannot host more than one workspace, which is the common case here.
- **Keep environments separate from workspaces.** Two objects with a one-to-one mapping would only
  add a join. Folding the environment in keeps a single admin surface.

## Unresolved questions

- Should memory become per workspace, per user and workspace, or stay per user?
- Repository ownership keys by `owner/name` today; keying by GitHub repository id would survive
  renames and transfers. Worth doing before public repositories rely on it?
- Which settings should inherit from `default` when that lands, and which must not, such as MCP
  connections?
- Confirm the follow-up scope for public workspaces listed under non-goals.

## Resolution

Pending.
