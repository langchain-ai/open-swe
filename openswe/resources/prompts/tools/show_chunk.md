Show the reader a chunk from the plan with your explanation, its code and a "Looks good" button.

- `number`: the chunk to show, as the walkthrough status numbers them, for when the reader asks to jump ahead or go back. Leave it out to show the reader's next chunk.

Only the lines the reader has not approved or skipped are offered for approval. A chunk already on screen and not yet approved goes back to the reader's list. Fails when no planned chunk is left for the reader; place one with `plan_chunk` first. After it posts, end your turn and wait for the reader.
