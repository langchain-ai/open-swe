Read or set a repository's approval mode as a currently authorized workspace admin when the user requests a settings change. Reads require a private admin surface. Writes also work in sole-writer shared threads; mixed Slack channel threads require the saved admin owner's authenticated run and exact-action approval. Shared writes return only an acknowledgement, and authorization is rechecked on every call.

Approval criteria live in the repository's `.open-swe/APPROVALS.md`, read from the pull request's base commit; this tool does not edit them. Change the file through a pull request instead.

- `action`: `read` or `set`.
- `repository`: `owner/repo`. Repository access is required.
- `mode`: for `set`, one of:
  - `off`: no approval assessment.
  - `dry_run`: post the assessment as a comment, including "Would approve", without approving. This is the default.
  - `approve`: submit a GitHub approval when the assessment would approve and the reviewed commit is still current. Set only when the user explicitly asks to submit GitHub approvals.
  - `null`: reset to the default (`dry_run`).

No merge is performed in any mode. `read` also reports whether `.open-swe/APPROVALS.md` exists on the default branch; without it, no mode produces an assessment.
