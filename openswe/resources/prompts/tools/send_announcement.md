DM an announcement from the Open SWE bot to every user with a linked Slack account, such as news of a feature that just shipped.
Requires a currently authorized admin on a private admin surface (a private dashboard thread or the admin's own Slack DM with the bot), started by the admin directly.
Use only when the admin explicitly asks to message everyone.

- `text`: the exact message, in Slack mrkdwn, at most 3000 characters.
  Draft it with the admin; do not add content they did not ask for.
- `confirm`: omit first.
  The call then sends nothing and returns `{"status": "preview", "recipients", "text"}`.
  Show the admin the exact text and the recipient count, and call again with `confirm=true` and the same text only after they explicitly approve it.
  Any change to the text needs a new preview and approval.

A confirmed call queues the DMs and returns `{"status": "sending", "recipients"}`; delivery runs in the background, paced to Slack's rate limits, and progress is visible in the dashboard's Admin → Users page.
Each person receives each announcement at most once.
A sent announcement cannot be recalled.
