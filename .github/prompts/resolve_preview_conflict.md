The current directory is the preview deployment tree being assembled: `HEAD` is `main` plus every branch that merged cleanly. The input below lists branches, one `<sha> <message>` per line, that conflicted with it. Merge all of them into `HEAD`, in order.

For each branch, run `git merge --no-ff -m "<message>" <sha>`, resolve every conflict so the result keeps the intent of both sides, `git add` the files you changed, and `git commit --no-edit`. Read both versions and the history (`git log --merge -p`, `git show MERGE_HEAD`, `git diff`) before editing. When both sides change the same code, combine them; when one side moved or renamed code the other side edited, carry the edit to its new location. Change files outside the conflicted ones only when the merged code would not build or run without it, and include that in the same merge commit.

If you cannot resolve a branch confidently, run `git merge --abort` and continue with the next one.

Never rewrite or drop existing commits, push, or stash (`git reset`, `git rebase`, `git commit --amend`, `git push`, `git stash`). Uncommitted changes are discarded when you finish.

Report with `cli_result`: `stdout` is one line per branch saying whether it merged and how its conflicts were resolved, and `exit_code` is 0 when every branch merged, 1 otherwise.
