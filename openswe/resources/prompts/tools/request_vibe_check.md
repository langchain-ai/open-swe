Run a vibe check on a GitHub pull request: TypeSafe's System One model judges each changed file's diff alone against fixed yes/no rules (debug leftovers, commented-out code, secrets, swallowed errors, silent fallbacks, stubs, narrating comments, test special-casing, plus warnings such as weakened tests and lint suppressions).
It takes seconds, posts or updates one "Vibe check" comment on the pull request, and returns each flagged file and rule.
Use it when someone asks for a vibe check, for example "do a vibe check review" or "vibe check this PR"; it is separate from the full automated review (`request_pr_review`) and does not start one.
Authors who enable Vibe check under Settings → Experiments get one automatically on every push to their pull requests.
Report the verdict and the comment link; a flag is a judgment from a diff alone, not a confirmed bug.
