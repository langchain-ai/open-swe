Read, save, or reset the advisory approval policy independently of review guidelines.
Only use this from a private admin task when the user requests a settings change.

- `action`: `read`, `save`, or `reset`. Read before editing to preserve intended requirements.
- `policy`: full replacement instructions for `save`, at most 10,000 characters.
- `repository`: optional `owner/repo`; replaces the inherited approval policy for that repo.
- `workspace`: optional workspace slug; overrides the instance policy using existing settings inheritance.
  Omit both repository and workspace to manage the instance policy; do not provide both.

Reset removes that override. These settings only affect future advisory assessments,
never GitHub approvals or merges. Repository access is required for repository settings.
