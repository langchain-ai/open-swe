Draft a GitHub review of the whole pull request for the user to submit as themselves.

Nothing is submitted by this call. The user sees the draft in the chat, can
change the verdict or edit the text, and chooses whether to submit it. Only
draft a review when the user asks for one or agrees to your offer to. Write the
body as the user would, in the first person, without mentioning that you
drafted it. Use `propose_review_comment` instead for feedback on specific lines;
the submitted review includes every comment in the user's pending review.

Args:
    event: `APPROVE`, `REQUEST_CHANGES`, or `COMMENT`.
    body: Review summary in GitHub Markdown. Required unless approving.

Returns:
    `{proposed: true, event, body}`, or `{proposed: false, error}` when the
    input is invalid. GitHub rejects approving or requesting changes on the
    user's own pull request; the user sees that error if they submit.
