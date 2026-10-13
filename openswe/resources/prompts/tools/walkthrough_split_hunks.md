Split unplanned hunks into smaller ones, each starting at a changed line you name, so a chunk can take part of what git shows as one hunk.

- `files`: per `path`, `at` lists the changed lines to start a new hunk at, written `+12` for added head line 12 or `-40` for deleted merge-base line 40. Each must be an unplanned line that does not already start a hunk.

The hunk the line sat in ends just before it, and the new hunk is named by that line, such as `+12`. Split where the change turns to a different idea: two unrelated edits git merged because they sit a few lines apart, or the separate classes and functions of a large new file. A split right before the first added line of a replacement separates the removed lines from the added ones, so split before the removed lines instead.

Returns the plan as it now stands.
