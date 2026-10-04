This checkout is the preview deployment being assembled: `main` plus every labelled pull request, merged together. The combined tree fails the dashboard typecheck, and stdin holds the failing command and its output. Earlier conflict resolutions in this tree may be wrong, such as an import that points at a module that no longer exists.

Fix the errors so the result behaves the way every merged pull request intended, then commit the fix on `HEAD`. Change only what the errors require. Do not revert a pull request's change to make the errors go away. Whatever is committed on `HEAD` when you finish is what deploys.

Verify with `pnpm --filter open-swe-dashboard run typecheck` before committing. Dependencies are already installed; do not run install scripts.

Finish with `cli_result`: `exit_code` 0 if the typecheck passes after your commit, 1 otherwise, and a one-sentence `stdout` saying what you changed.
