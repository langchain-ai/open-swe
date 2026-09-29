Record the chunk on screen as reviewed and commit it. Call it only when the reader's latest message says the chunk looks good.

The staged changes must be exactly what the last `review_reply(chunk=true)` showed; restaging after it is refused. Approved lines are remembered for this reader, so they are never shown again, even after the pull request is rebased or force-pushed.

- `title`: a short name for the chunk, roughly 4-10 words.

Returns the diffstat of what is still left to review.
