Skip chunks the reader chose not to see. Call it only when the reader asked to skip them.

- `chunks`: the chunk numbers to skip, as `plan_walkthrough` returned them.
- `reason`: what the reader said, in a few words.
- `skip_other`: `true` to skip Other as well, such as when the reader is done.

Skipped chunks are not remembered as reviewed, and the closing message and any approval name them.
