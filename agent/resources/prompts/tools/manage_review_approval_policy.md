Read, save, or reset approval settings independently of review guidelines.
Only use this from a private admin task when the user requests a settings change.

- `action`: `read`, `save`, or `reset`. Read before editing to preserve intended requirements.
- `policy`: full replacement instructions for `save`, at most 10,000 characters.
- `auto_approve`: optional boolean or `"inherit"` for `save` at the instance or workspace level. Enable only when the user explicitly asks to submit GitHub approvals. A configured policy alone remains advisory; no effective policy means no assessment or approval. Use `"inherit"` to remove the override (off at the instance). Omit this field to preserve it.
- `repository`: optional `owner/repo`; replaces the inherited approval policy for that repo.
- `workspace`: optional workspace slug; overrides the instance policy using existing settings inheritance.
  Omit both repository and workspace to manage the instance policy; do not provide both.

Reset removes that override. The approval toggle defaults off. When on, eligible assessments may submit a GitHub approval; no merge is performed. Repository access is required for repository settings.
