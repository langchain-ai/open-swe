List this workspace's memories with one status, oldest first.

You only ever see memories saved in shared threads; what private threads remember never reaches you.
`pending` memories wait for your decision.

Each memory names its `author`: their full name, GitHub login and id, Slack user id, and whether they are a configured `admin`.
Count different authors to tell many voices from one.
`thread_id` is where it was saved; read that thread when you need evidence.
`withdrawal_requested` means its author asked to take it back, and it is pending again even if you adopted it before.

Args:
- status: `pending` (the default), `adopted` or `declined`.
