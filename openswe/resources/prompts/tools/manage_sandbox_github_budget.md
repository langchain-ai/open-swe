Read or set the budget on sandboxes' GitHub API calls made with Robin's GitHub App installation token.
Requires a currently authorized workspace admin.
`read` requires a private admin surface; `set` also works in a verified sole-writer shared thread, returning only an acknowledgement.
Authorization is rechecked on every call.
Use only for an explicit request to change the budget.

Every sandbox on the installation's token spends the installation's one hourly GitHub API quota, which Robin's own reviews and approvals need too.
The budget is instance-wide; workspaces cannot override it.
`git` over HTTPS is never metered.

- `action`: `read` or `set`.
- `settings`: for `set`, any of:
  - `mode`: `off` (no metering), `observe` (each API call goes through the backend, which counts and logs it but never refuses), or `enforce` (as `observe`, and over-budget calls are refused).
    `observe` and `enforce` make LangSmith's proxy call the backend for every sandbox GitHub API call instead of reusing its answer for 5 minutes.
    A change reaches a sandbox at its thread's next run.
  - `per_hour`: API calls one thread may make in any 60 minutes before `enforce` refuses more; at least 1.
  - `brake_percent`: 0 to 100; while less than this share of the installation's hourly quota is left, `enforce` refuses every sandbox call until the quota resets.

  A field passed as null restores its default (`observe`, 500, 20).
  Omit `settings` to read.
