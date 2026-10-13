Send unplanned hunks to Other without placing a chunk, or take hunks back out of Other.

- `files`: the hunks, per `path`, named in `hunks` as for `walkthrough_plan_chunk`.
- `restore`: `true` to take these hunks out of Other, so they are unplanned again and can go in a chunk.

Use it for noise, such as every hunk of a lockfile or an imports-only hunk, or when a hunk you had put in Other turns out to matter.
