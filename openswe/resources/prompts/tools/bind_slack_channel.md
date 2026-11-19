Bind a Slack channel to Robin and choose how ordinary messages start conversations.
Requires an authorized admin; supported writes also work in verified sole-writer shared threads, returning only an acknowledgement.

- `channel_id`: the Slack channel to bind; Robin must have joined it
- `mode`: `kitchen` for ordinary top-level messages, or `ask` for investigation and recommendations that wait for a human choice before filing tickets or implementing fixes

This changes integration behavior, not execution defaults, personal kitchen preferences, credentials, or sandbox images.
