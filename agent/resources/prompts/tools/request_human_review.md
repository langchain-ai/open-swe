Ask people in Slack to review any open GitHub pull request. It posts a card with the pull request's title, your `inline_summary`, and an "I'll review" button in the repository's review channel (`reviewChannel` in `.open-swe/settings.json`). Anyone may sign up, and the pull request merges on its own once every reviewer approves on GitHub, or two hours after the request with at least one approval, as long as checks pass and nothing is unresolved.

Use it when someone asks for a human review, or when a pull request you opened is ready for one. It is refused while the pull request is a draft, has merge conflicts, or is failing a required check. When someone asks you to get a draft reviewed, mark it ready with `gh pr ready` and then call this; do not ask them first. For a tiny change that someone can approve straight from the diff, use `expedite_pr_approval` instead.

`inline_summary` is required: one or two plain sentences, at most 280 characters, on what the change does and why, as a reviewer wants to know before opening it. Write it only from the pull request itself, read in this run with `gh pr view <url> --json title,body` and `gh pr diff <url>`; never from the branch name, the title alone, or what you remember of other pull requests. No pull request numbers, URLs, SHAs, file paths or names, and no request to review it.

Pass `channel` (a Slack channel name like `#eng-reviews` or a channel id) to post somewhere other than the repository's review channel; it is required when the repository has none. When this conversation is already a thread in that channel, the card is posted in the thread and also sent to the channel; otherwise it is posted in that channel and this thread gets a one-line pointer to it.

The card is the announcement. Do not follow it with a status update, and never link to the card: Slack unfurls that link into a second copy of it.

If nobody signs up within 30 minutes you are woken to pick a reviewer with `assign_human_reviewer`. Calling this again for a pull request with an open request returns the existing card; when the request came from this thread, the card's summary is replaced with the new `inline_summary`, which is how you correct it.
