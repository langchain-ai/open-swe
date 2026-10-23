Fix the following issue in the $repo repository.

Clone $clone_url into the directory named exactly $checkout_dir inside your
working directory, for example `git clone $clone_url $checkout_dir`. Do not use
the repository's default directory name. Check out the exact base commit
$base_commit before changing code.

Issue:
$problem_statement

Make the code changes needed to fix the issue, and validate them with the
repository's own tests where practical. Do NOT open a pull request, push a
branch, or otherwise write to GitHub. Leave the changes in $checkout_dir;
the runtime collects the patch without another model call.
