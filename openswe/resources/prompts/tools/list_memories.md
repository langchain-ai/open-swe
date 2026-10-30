List this workspace's memories with one status, oldest first.

`pending` memories wait for your decision.
`held` ones came from private threads whose people did not share them: you see only that they exist, never what they say, and you never decide them.

Each memory has an opaque `person` that is the same for everything one person saved here, so you can count how many different people say a thing without learning who they are.
`thread_id` is where a memory from a public thread was saved; read that thread when you need evidence.
`withdrawal_requested` means its person asked to take it back, and it is pending again even if you adopted it before.

Args:
- status: `pending` (the default), `held`, `adopted` or `declined`.
