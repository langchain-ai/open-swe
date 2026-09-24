Read, save, or reset approval settings independently of review guidelines.
Only use this from a private admin task when the user requests a settings change.

- `action`: `read`, `save`, or `reset`. Read before editing to preserve intended requirements.
- `policy`: full replacement instructions for `save`, at most 10,000 characters.
- `repository`: optional `owner/repo`; replaces the inherited approval policy for that repo.
- `workspace`: optional workspace slug; overrides the instance policy using existing settings inheritance.
  Omit both repository and workspace to manage the instance policy; do not provide both.

Reset removes that override. Assessments are advisory only: reviews never submit GitHub approvals, so no approval toggle exists. Repository access is required for repository settings.
