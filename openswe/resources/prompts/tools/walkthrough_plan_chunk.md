Place one chunk of unplanned lines in the walkthrough plan, and optionally send other lines to Other in the same step.

- `title`: roughly 4-10 words naming the chunk, identifiers in `backticks`.
- `show`: the chunk's lines, per `path`: `added` as inclusive `[start, end]` ranges of head line numbers and `deleted` as ranges of merge-base line numbers, as the pull request's `git diff` hunk headers number them.
- `explanation`: two to four plain sentences on what the chunk changes and why. The code is rendered from its lines, so never include code.
- `other`: lines to move to Other now, such as the imports above the class the chunk shows. Same shape as `show`.
- `after`: the number of the chunk this one follows, `0` to put it first. Leave it out to add it last.

Returns the chunk's number and the plan as it now stands.
