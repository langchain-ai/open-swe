Repo prep FAILED: the checkout in `$working_dir` may be missing or — worse — present but stale (at an old commit). Do NOT trust local files until you have re-prepped the tree yourself. Run:

```
cd $working_dir || { cd $parent_dir && gh repo clone $repo_owner/$repo_name && cd $repo_name; }
git fetch origin $head_sha --quiet || git fetch origin refs/pull/$pr_number/head --quiet
git checkout --force $head_sha --quiet
```

and verify `git rev-parse HEAD` matches the PR head before reading local files. If you cannot get the tree onto the PR head, rely exclusively on the diff and file contents from `gh api repos/$repo_owner/$repo_name/contents/<path>?ref=$head_sha` — never on the local checkout.
