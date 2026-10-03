Read a repository's approval mode when the user requests a settings change. Requires a currently authorized workspace admin on a private admin surface. Authorization is rechecked on every call.

Approval criteria live in the repository's `.open-swe/APPROVALS.md`, read from the pull request's base commit. Assessments are advisory only: reviews always publish as comments and never submit a GitHub approval, whatever the stored mode. Changing the file or the mode requires a pull request; this tool can no longer write either.

- `action`: `read`.
- `repository`: `owner/repo`. Repository access is required.

`read` also reports whether `.open-swe/APPROVALS.md` exists on the default branch; without it, no assessment is produced.
