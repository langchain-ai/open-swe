Ask people in Slack to review any open GitHub pull request. It posts a card with the pull request's title, your `inline_summary`, and an "I'll review" button in the repository's review channel (`reviewChannel` in `.open-swe/settings.json`). Anyone may sign up, and the pull request merges on its own once every reviewer approves on GitHub, or two hours after the request with at least one approval, as long as checks pass and nothing is unresolved.

Use it when someone asks for a human review, or when a pull request you opened is ready for one. It is refused while the pull request is a draft, has merge conflicts, or is failing a required check: fix those first. For a tiny change that someone can approve straight from the diff, use `expedite_pr_approval` instead.

`inline_summary` is required: one or two plain sentences, at most 280 characters, on what the change does and why, as a reviewer wants to know before opening it. No pull request numbers, URLs, SHAs, file paths or names, and no request to review it.

Pass `channel` (a Slack channel name like `#eng-reviews` or a channel id) to post somewhere other than the repository's review channel; it is required when the repository has none. When this conversation is already a thread in that channel, the card is posted in the thread and also sent to the channel.

The card is the announcement. Do not follow it with a status update, and never link to the card or the returned `permalink`: Slack unfurls that link into a second copy of the card. If you must reply, use one short line with no links, naming the channel only when the card went somewhere other than this thread.

If nobody signs up within 30 minutes you are woken to pick a reviewer with `assign_human_reviewer`. Calling this again for a pull request with an open request returns the existing card.
