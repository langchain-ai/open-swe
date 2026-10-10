Place one chunk of unplanned hunks in the walkthrough plan, and optionally send other hunks to Other in the same step.

- `title`: roughly 4-10 words naming the chunk, identifiers in `backticks`.
- `show`: the chunk's hunks, per `path`: each named in `hunks` by the head line its `@@ -a,b +N,c @@` header starts at, `N`, as the plan status lists them. Gather related hunks from several files into one chunk.
- `explanation`: two to four plain sentences on what the chunk changes, why, and how its hunks fit together. The code is rendered from its hunks, so never include code.
- `other`: hunks to move to Other now, such as an imports-only hunk in a file the chunk shows. Same shape as `show`.
- `after`: the number of the chunk this one follows, `0` to put it first. Leave it out to add it last.

Returns the chunk's number and the plan as it now stands.
