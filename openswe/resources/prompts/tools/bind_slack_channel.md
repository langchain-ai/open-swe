Bind a Slack channel to Robin and set its editable channel instructions.
Requires an authorized admin; supported writes also work in verified sole-writer shared threads, returning only an acknowledgement.

- `channel_id`: the Slack channel to bind; Robin must have joined it
- `prompt`: instructions for what Robin should respond to, how to respond, and any DRI or escalation guidance; use an empty string for standard behavior

Instructions apply to untagged messages, explicit mentions, and replies.
Updating a binding replaces its instructions; Slack topics, purposes and descriptions do not configure Robin.
Instructions do not grant credentials, repository access, or tool permissions.

This changes integration behavior, not execution defaults, credentials, or sandbox images.
