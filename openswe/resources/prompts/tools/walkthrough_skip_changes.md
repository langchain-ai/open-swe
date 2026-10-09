Skip changes the reader chose not to see. Call it only when the reader asked to skip them.

- `reason`: what the reader said, in a few words.
- `chunks`: the chunks to skip, by number. Leave it out to skip everything the reader has left, unplanned lines included.
- `include_other`: `true` to skip Other as well, such as when the reader is done.

Skipping is the reader's own: the plan stays as it is for everyone else. Skipped lines are not remembered as reviewed, and the closing message and any approval say what was skipped and why.
