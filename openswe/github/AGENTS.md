# GitHub integration

## Request budgets and caching

- Cap requests per action or event burst, including pagination and helper calls.
  Reuse PR snapshots within an operation, batch related reads, and share identical in-flight reads only when access scope permits.
- Coalesce refreshes per entity without dropping the final update.
  Assert request budgets in existing behavioral tests when changing paths that amplify calls.
- Key caches and scope reuse by authorization, repository, base/head commits, and requested fields.
  Track pagination separately so truncated results cannot satisfy fuller reads.
  Bound cache size and keep cancellation from stranding other callers.
  See `changed_files.py` for an example.

## Failure semantics

- Distinguish absence, denial, rate limits, transient failures, and incomplete or unknown results.
  Reuse `GitHubError` classification where applicable; a 403 alone does not prove missing repo access.
- Accept partial GraphQL responses only under an explicit field-level policy, as in `RepoClient.ci_graphql`.
  Keep missing facts unknown, make conservative decisions, and surface unrelated errors.
- Keep privacy-sensitive public errors opaque; diagnose internally without exposing credentials or private resource existence.
