Repo prep FAILED: the checkout in `$working_dir` may be missing or — worse — present but stale (at an old commit). Do NOT trust local files until you have re-prepped the tree yourself. Run:

```
if git -C $working_dir rev-parse --git-dir >/dev/null 2>&1; then
  cd $working_dir
elif [ -d $working_dir ]; then
  cd $working_dir
  git init --quiet
  if git remote get-url origin >/dev/null 2>&1; then git remote set-url origin https://github.com/$repo_owner/$repo_name.git; else git remote add origin https://github.com/$repo_owner/$repo_name.git; fi
else
  cd $parent_dir && gh repo clone $repo_owner/$repo_name && cd $repo_name
fi
git fetch origin $head_sha --quiet || git fetch origin refs/pull/$pr_number/head --quiet
git checkout --force $head_sha --quiet
```

and verify `git rev-parse HEAD` matches the PR head before reading local files. If you cannot get the tree onto the PR head, rely exclusively on the diff and file contents from `gh api repos/$repo_owner/$repo_name/contents/<path>?ref=$head_sha` — never on the local checkout.
