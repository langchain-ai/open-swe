# Auto-approve rules

A repository tells Open SWE which pull requests it may approve with Markdown files in `.open-swe/auto-approve-rules/`, one rule per file.
Open SWE reads them from the repository's default branch, so a pull request cannot rewrite the rules it is judged by.

```markdown
---
paths:
  - docs/*.md
  - README.md
---
Open SWE may approve a pull request that only changes documentation.
```

- The Markdown body is the rule, written for the reviewer to apply.
- `paths` is optional: a list of globs matched with Python's `fnmatch`, where `*` also matches `/`.
  A rule with `paths` applies only when every file the pull request changes, including a renamed file's old path, matches at least one glob.
  A rule without `paths` applies to every pull request, which suits limits that must always hold, such as changes that always need human review.
- A rule with invalid frontmatter or an empty body is ignored.

The reviewer assesses a pull request against the rules that apply to it, and `expedite_pr_approval` may leave hunks those rules cover off its card ([expedited Slack review](expedited-slack-review.md)).
The repository's approval mode decides what an approving assessment does: `off` skips the assessment, `dry_run` (the default) posts it as a comment, and `approve` submits a GitHub approval.
Without any rule on the default branch, reviews post no approval assessment.
See this repository's own [`.open-swe/auto-approve-rules/`](../../.open-swe/auto-approve-rules) for examples.
