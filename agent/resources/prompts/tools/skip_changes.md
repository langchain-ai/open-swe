Skip changes the reader chose not to see. Call it only when the reader asked to skip them.

- `reason`: what the reader said, in a few words.
- `files`: the lines to skip, per `path`, with `added` and `deleted` ranges. Leave it out to skip everything still left.
- `include_other`: `true` to skip Other as well, such as when the reader is done.

Skipped lines are not remembered as reviewed, and the closing message and any approval say what was skipped and why.
