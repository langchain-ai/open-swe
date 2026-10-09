Send unplanned lines to Other without placing a chunk, or take lines back out of Other.

- `files`: the lines, per `path`, with `added` and `deleted` ranges numbered as for `walkthrough_plan_chunk`.
- `restore`: `true` to take these lines out of Other, so they are unplanned again and can go in a chunk.

Use it for noise, such as a whole lockfile or every import in a file, or when a line you had put in Other turns out to matter.
