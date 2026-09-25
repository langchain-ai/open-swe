A `git merge` in the current directory stopped on conflicts while assembling the preview deployment. `HEAD` is the preview tree so far: `main` plus the branches merged before this one. `MERGE_HEAD` is the branch being merged. The input below names the merge and the conflicted files.

Resolve every conflict so the result keeps the intent of both sides. Read both versions and the history (`git log --merge -p`, `git show MERGE_HEAD`, `git diff`) before editing. When both sides change the same code, combine them; when one side moved or renamed code the other side edited, carry the edit to its new location. Change files outside the conflicted ones only when the merged code would not build or run without it.

Then `git add` every file you changed; unstaged changes are discarded.

Never commit, abort the merge, push, or move `HEAD` (`git commit`, `git merge --abort`, `git reset`, `git checkout <commit>`, `git stash`). The preview build commits the merge itself once you finish.

If you cannot resolve a conflict confidently, leave it unresolved.

Report with `cli_result`: `stdout` is one line per conflicted file saying how it was resolved, and `exit_code` is 0 when every conflict is resolved and staged, 1 otherwise.
