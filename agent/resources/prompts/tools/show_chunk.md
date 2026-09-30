Show the reader one chunk you picked just now, with a "Looks good" button, and optionally send other lines to Other in the same step.

- `title`: roughly 4-10 words naming the chunk, identifiers in `backticks`.
- `show`: the chunk's lines, per `path`: `added` as inclusive `[start, end]` ranges of head line numbers and `deleted` as ranges of merge-base line numbers, as the pull request's `git diff` hunk headers number them. A range may span lines that are unchanged or already dealt with; only the lines still left in it are shown, and every range must hold at least one.
- `explanation`: two to four plain sentences on what the chunk changes and why. The server renders the code below it, so never include code. It is a Jinja template; see the system prompt for its helpers.
- `other`: lines to move to Other now, such as the imports above the class you are showing. Same shape as `show`.

A chunk already on screen and not yet approved goes back to what is left. After it posts, end your turn and wait for the reader.
