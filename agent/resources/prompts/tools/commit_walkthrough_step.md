Commit everything currently staged as the next step of the review walkthrough.

Stage the step's changes first with `git add` or `git apply --cached`; the call fails when nothing is staged. Steps appear in the order you commit them.

Your first commit must be the `other: true` pass. Any other commit before it is refused.

- `title`: short headline naming the change, roughly 4-10 words, no trailing punctuation. Wrap code identifiers in `backticks`.
- `summary`: a few plain sentences on what this step changes and why. Wrap identifiers, types, flags and paths in `backticks`. No code blocks or links.
- `other`: `true` for the first commit, which collects imports, generated files, formatting, mechanical repetition and every other change a reviewer does not need to read. It is always shown last and may be empty when nothing qualifies.
