Prepare an announcement from the Robin bot to every user with a linked Slack account, such as news of a feature that just shipped.
Requires a currently authorized admin in their own private dashboard admin thread or Slack DM, started directly by the admin.
Use only when the admin explicitly asks to message everyone.

- `text`: the exact message in Slack mrkdwn, at most 3000 characters.
  Draft it with the admin; do not add content they did not ask for.

The call sends nothing.
It returns the exact saved message, the frozen recipient count, an expiration time, and `approval_url`.
Tell the admin to open that link, review the message and audience, and approve or decline it themselves.
Approval cannot be given to the agent in chat.
A new call supersedes the prior pending request in this thread; changed text requires a new decision.

After approval, delivery runs in the background, paced to Slack's rate limits.
Progress and partial failures are visible in Admin → Users.
A queued announcement is not yet delivered, and a sent announcement cannot be recalled.
