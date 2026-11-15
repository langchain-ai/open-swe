Read the pull request's diff as the plan names it: each hunk headed with its name, size and where it is in the plan, and every changed line labelled with the name the planning tools take.

- Without `paths`: every unplanned hunk, file by file in diff order, with lockfiles listed but not shown.
- `paths`: files, directories or globs such as `src/api/` or `**/*.test.ts`; shows every hunk of the matching files, planned or not.
- `start`: where an earlier read stopped, exactly as its last line gives it; pass the same `paths` again.

A hunk's heading reads `── hunk +12 (+3/-1): unplanned`, where `+12` is the name to pass in `show`, `other` or `hunks`; its status is `unplanned`, `chunk 2` or `Other`.
Each changed line starts with its own name, `+12` for added head line 12 or `-40` for deleted merge-base line 40, which is what `walkthrough_split_hunks` takes in `at`; unchanged context lines have no name.

Each read returns about 60,000 characters and ends by saying how to continue when more follows.
Use this rather than `git diff` to survey and plan the change; read files at the head with the shell when you need more than the diff's context.
