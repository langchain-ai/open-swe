Inspect or stop background work: sandbox commands and workspace refreshes.

`status`, `stop`, and `wait` require `task_id`; `list` does not. `wait` blocks for
up to the requested bounded timeout (120 seconds by default, capped at 300) and
returns when the task finishes or the deadline expires. A refresh's status carries
`steps` — which stage it reached — and, while a script is running, a tail of that
script's live `bash -x` trace read off the builder sandbox; workspace refreshes
are visible only to workspace admins. Status reads are for explicit user requests,
for following a long rebuild, or when completion needs inspection — not for
polling loops. Never repeatedly call `status` to wait; use `wait` or end the turn
and rely on the automatic completion notification.
