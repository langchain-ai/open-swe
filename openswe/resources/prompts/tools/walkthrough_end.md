End the walkthrough.

When every chunk and Other has been approved or skipped, it posts `message` (if any), the server's account of how much of the pull request the reader saw, and the closing button: "Approve" for a reviewer, "Mark ready" for an author whose pull request is still a draft. When the walkthrough is not finished, it posts only `message`, and nothing at all without one.

- `message`: optional. Leave it out unless there is something the reader needs to hear; a closing summary is only worth it when it adds something.
- `archive`: `true` to also close the session and archive the channel, for good: nothing wakes the guide afterwards, not even a pull request update. Use it when the reader asks you to stop, cancel or close the review, or when carrying on no longer makes sense, such as the pull request being closed or merged.
