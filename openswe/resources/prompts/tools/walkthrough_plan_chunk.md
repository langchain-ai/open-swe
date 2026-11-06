Place one chunk of unplanned hunks in the walkthrough plan, and optionally send other hunks to Other in the same step.

- `title`: roughly 4-10 words naming the chunk, identifiers in `backticks`.
- `show`: the chunk's hunks, per `path`: each named in `hunks` by its first changed line, such as `+12` or `-40`, as the plan status lists them.
  Gather related hunks from several files into one chunk; split a hunk first with `walkthrough_split_hunks` to take only part of it.
- `explanation`: two to four plain sentences on what the chunk changes, why, and how its hunks fit together.
  The code is rendered from its hunks, so never include code.
- `notes`: optional, at most five, for the chunk's trickiest lines only: each a `path`, a `text` and a `note` of one or two sentences pinned beside the line.
  Pick the line the way an edit picks its old text: `text` is copied exactly from the chunk's changed lines in `path`, one line or several, and must match exactly one place there; the note goes on the last line it covers.
  Include more of the surrounding lines when a short `text` matches more than once.
  Leave them out for straightforward code.
- `other`: hunks to move to Other now, such as an imports-only hunk in a file the chunk shows.
  Same shape as `show`.
- `replace`: existing chunk numbers to drop and replace with this chunk, for example to merge several steps or revise a previous step.
  Their hunks become available in `show` and `other`; omitted hunks return to unplanned, never silently to Other.
  Use the latest plan's numbers.
- `after`: the number of the chunk this one follows after replacements are removed, `0` to put it first.
  Leave it out to reuse the first replaced chunk's position, or add it last when not replacing.

Returns the chunk's number and the plan as it now stands.

In a flagged implementation thread, pass `pr_url` with the linked PR’s canonical URL to each planning tool.
Call this tool with only `pr_url` first to pin the latest head and get the unplanned hunks and shared planning guidance without placing a chunk.
The result includes `complete` and `head_sha`; finish planning the latest pushed head before delivering the PR.
