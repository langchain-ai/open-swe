Dismiss a pull request's open human review request in Slack, exactly as clicking its card's Dismiss button does: the card is marked dismissed, a copy sent to the channel from a thread is removed, and the pull request no longer merges on its own. Pass the pull request URL and, optionally, `reason`: a few words shown on the card.

Use it when someone asks you to take a review request down, or when the card is wrong and a fresh request is better than correcting its summary with `request_human_review`. It does not close the pull request.
