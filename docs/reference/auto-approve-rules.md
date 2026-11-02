# Auto-approve rules

A repository tells Open SWE which pull requests it may approve with Markdown files in `.open-swe/auto-approve-rules/`, one rule per file.
Open SWE reads them from the repository's default branch, so a pull request cannot rewrite the rules it is judged by.

```markdown
---
match-all-files:
  - docs/*.md
  - README.md
---
Open SWE may approve a pull request that only changes documentation.
```

- The Markdown body is the rule, written for the reviewer to apply.
- Two optional frontmatter keys scope a rule, each a list of globs matched with Python's `fnmatch`, where `*` also matches `/`.
  Changed files include a renamed file's old path.
  - `match-all-files`: the rule applies only when every changed file matches at least one glob. Use it for rules that grant approval to one kind of change.
  - `match-any-files`: the rule applies when at least one changed file matches. Use it for restrictions on part of the repository, such as requiring human review for changes under `auth/`.
  - With both, both must hold; with neither, the rule applies to every pull request.
- If any rule on the default branch cannot be read or parsed (invalid YAML, an unknown frontmatter key, an empty body), no rule applies: the review says why auto approval did not run, and nothing is approved or left off an expedited card.
- When a pull request changes `.open-swe/auto-approve-rules/` or `.open-swe/settings.json` and the result is invalid, Open SWE's review requests changes and lists the problems.

The reviewer assesses a pull request against the rules that apply to it, and `expedite_pr_approval` may leave hunks those rules cover off its card ([expedited Slack review](expedited-slack-review.md)).
The repository's approval mode decides what an approving assessment does: `off` skips the assessment, `dry_run` (the default) posts it as a comment, and `approve` submits a GitHub approval.
Without any rule on the default branch, reviews post no approval assessment.
See this repository's own [`.open-swe/auto-approve-rules/`](../../.open-swe/auto-approve-rules) for examples.
