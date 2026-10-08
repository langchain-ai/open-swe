Open a new Slack code channel and walk the person asking through a pull request there, a small chunk at a time, answering their questions as they go.

It copies this conversation, with everything you know, into a new thread bound to the channel, sharing this sandbox; that copy of you runs the walkthrough. This thread stays as it is.

Use it only when someone explicitly asks for a review channel, or to be walked, guided, or taken through a pull request in one. Never start one on your own initiative, never suggest one, and never treat a request to review, summarize, or explain a pull request as a request for one. A reviewer ends by approving it on GitHub. The pull request's own author, for example getting to know what an agent did for them, ends by marking a draft ready for review, can ask for changes along the way, and the feedback they give is recorded for reviewers.

- `pr_url`: the pull request, as `https://github.com/<owner>/<repo>/pull/<number>`.
- `invite`: anyone else to add to the channel, as Slack user ids. The person asking is always added.

Reply with the channel link from the result.
