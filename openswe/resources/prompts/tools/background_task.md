Inspect or stop background work: sandbox commands and global execution image builds.

`status` and `stop` require `task_id`; `list` does not.
An image build carries `steps` and, while a script runs, a tail of its live `bash -x` trace from the builder sandbox.
Image tasks use `image-` IDs and are available only in a private admin conversation; shared threads can still inspect their own sandbox commands.
Status reads are for explicit requests, following a long rebuild, or inspecting completion, not polling loops.
