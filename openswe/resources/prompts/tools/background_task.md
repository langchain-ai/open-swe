Inspect or stop background work: sandbox commands, Robin-wide image builds and legacy workspace refreshes.

`status` and `stop` require `task_id`; `list` does not.
An image build or refresh carries `steps` — which stage it reached — and, while a script is running, a tail of that script's live `bash -x` trace read off the builder sandbox.
These tasks are visible only to workspace admins; Robin-wide builds use `image-` task IDs.
Status reads are for explicit user requests, for following a long rebuild, or when completion needs inspection — not for polling loops.
