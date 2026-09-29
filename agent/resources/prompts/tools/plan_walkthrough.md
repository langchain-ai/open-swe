Replace the walkthrough plan with these chunks, in the order the reader will see them.

- `chunks`: each has a `title` (roughly 4-10 words, identifiers in `backticks`) and `files`, the line ranges it shows: per `path`, `added` as inclusive `[start, end]` ranges of head line numbers and `deleted` as ranges of merge-base line numbers, as `read_changes` prints them. A range may span unchanged lines; only the changed lines in it are claimed.

Every unreviewed changed line not claimed by a chunk goes to Other. The call fails, and nothing changes, when a range holds no unreviewed changed line, a path is not in the pull request, a line is claimed twice, or a chunk claims nothing. It returns each chunk's line count and what Other holds, per file.

Lines the reader already approved are never part of a plan.
