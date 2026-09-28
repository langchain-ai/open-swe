Read or set a repository's approval mode. Only use this from a private admin task when the user requests a settings change.

Approval criteria live in the repository's root `APPROVALS.md`, read from the pull request's base commit; this tool does not edit them. Change the file through a pull request instead.

- `action`: `read` or `set`.
- `repository`: `owner/repo`. Repository access is required.
- `mode`: for `set`, one of:
  - `off`: no approval assessment.
  - `dry_run`: post the assessment as a comment, including "Would approve", without approving. This is the default.
  - `approve`: submit a GitHub approval when the assessment would approve and the reviewed commit is still current. Set only when the user explicitly asks to submit GitHub approvals.
  - `null`: reset to the default (`dry_run`).

No merge is performed in any mode. `read` also reports whether `APPROVALS.md` exists on the default branch; without it, no mode produces an assessment.
