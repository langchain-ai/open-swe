Build the shared walkthrough for a PR linked to this implementation thread, including drafts, without starting a scout.
Call with only `pr_url` first to pin the latest PR head and get its plan, remaining hunks, diff command, and planning guidance.
Then call repeatedly to place chunks (`title`, `show`, `explanation`, optional `other`, `after`, `replace`), split hunks (`splits`), move noise to Other (`other` without `show`), or set `other_summary`.
Hunks use the shared walkthrough's file paths and first changed lines, not arbitrary line ranges.
Finish all meaningful changes before announcing the PR: `complete: true` means every changed line is assigned.
Recheck after any subsequent push. Never mark the PR ready or request review just to build its walkthrough.
Requires `implementation_walkthrough_enabled`; only PRs already opened or linked in this thread can be planned.
