Read or set automatic Open SWE reviews for one repository. Requires a currently authorized workspace admin and access to that repository. `read` requires a private dashboard thread or authenticated Slack DM; `set` also works in a verified sole-writer shared thread, returning only an acknowledgement. Authorization is rechecked on every call.

- `action`: `read` or `set`.
- `repository`: full `owner/repo` name.
- `enabled`: for `set`, true to enable automatic reviews or false to disable them. Omit when reading.

Use only when the user explicitly asks to change repository auto-review settings. This changes the same instance-wide opt-in as the Code review dashboard. Other repositories, review styles, draft preferences, and approval modes remain unchanged. This does not trigger reviews of existing pull requests or enable automatic GitHub approvals; use `manage_review_approval_mode` for approval settings only when explicitly requested.
