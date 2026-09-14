Inspect or stop background work: sandbox commands and environment refreshes.

`status` and `stop` require `task_id`; `list` does not. A refresh's status carries
`steps` — which stage it reached — and, while a script is running, a tail of that
script's live `bash -x` trace read off the builder sandbox; environment refreshes
are visible only to workspace admins. Status reads are for explicit user requests,
for following a long rebuild, or when completion needs inspection — not for
polling loops.
