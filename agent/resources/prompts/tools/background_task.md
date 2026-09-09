Inspect or stop background sandbox commands.

`status` and `stop` require `task_id`; `list` does not. Status reads are for explicit user
requests or when completion needs inspection, not polling loops.
