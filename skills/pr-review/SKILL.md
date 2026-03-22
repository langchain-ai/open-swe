---
name: pr-review
description: Use when asked to review a pull request, leave feedback on a PR, check code quality, approve or request changes, or when @openswe is mentioned in a PR comment asking for a review.
---

# PR Review Skill

## Goal

Leave a structured GitHub review — not just a plain comment. Use the review tools to create inline feedback, approve, or request changes directly on the PR.

## Review Process

1. Call `list_pr_reviews` first — see what's already been reviewed so you don't duplicate feedback
2. Fetch the PR diff using `http_request`: `GET /repos/{owner}/{repo}/pulls/{pull_number}`
3. Read the changed files in the sandbox — clone the repo and read full file context, not just the diff
4. Create the review using `create_pr_review` with inline comments where possible — this is your only output
5. Keep the review body short: 2-4 sentences max. Inline comments should be 1-2 lines each.

## What to Look For

**Must flag — use REQUEST_CHANGES:**
- Security issues: hardcoded secrets, SQL injection, command injection, unvalidated user input
- Data loss risks: destructive operations without guards, missing DB migrations
- Broken logic: incorrect conditionals, off-by-one errors, unhandled edge cases
- Missing error handling at system boundaries (API calls, DB queries, file I/O)

**Should flag — use COMMENT (non-blocking):**
- Performance issues: N+1 queries, unnecessary loops, missing indexes
- Missing tests for new logic
- Unclear naming that hurts readability
- Dead code or unused imports

**Skip entirely:**
- Style preferences not enforced by a linter
- Subjective refactors outside the PR scope
- Minor formatting (let CI/linters handle it)

## Review Events — When to Use Each

- **APPROVE** — code is correct, safe, and ready to merge. No unresolved blocking concerns.
- **REQUEST_CHANGES** — there are blocking issues the author must fix before merge.
- **COMMENT** — feedback only, not blocking. Use for questions or suggestions.

Never APPROVE if you have an unresolved blocking concern. Never REQUEST_CHANGES for style nits.

## Available Tools

open-swe has the following PR review tools available:

| Tool | What it does |
|------|-------------|
| `list_pr_reviews` | List all reviews on a PR — always call this first |
| `get_pr_review` | Get a specific review by ID |
| `create_pr_review` | Create and submit a new review with optional inline comments |
| `update_pr_review` | Update the body of your previous review |
| `dismiss_pr_review` | Dismiss your own stale review after the author addresses feedback |
| `submit_pr_review` | Submit a pending (draft) review that was created without an event |
| `list_pr_review_comments` | List inline comments on a specific review or all PR review comments |

### create_pr_review — Parameters

```
pull_number: int          # PR number
event: str                # APPROVE | REQUEST_CHANGES | COMMENT
body: str                 # Top-level review summary (required for APPROVE and REQUEST_CHANGES)
comments: list            # Optional inline comments (see format below)
commit_id: str            # Optional — defaults to latest commit
```

Inline comment format:
```json
{
  "path": "agent/tools/github_review.py",
  "line": 42,
  "side": "RIGHT",
  "body": "This will fail if the token is expired — handle the 401 case."
}
```

For multi-line inline comments, add:
```json
{
  "start_line": 40,
  "start_side": "RIGHT",
  "line": 44,
  "side": "RIGHT"
}
```

## Updating Previous Reviews

When the author pushes new commits addressing your feedback:
1. Call `list_pr_reviews` to find your previous review ID
2. Call `dismiss_pr_review` with a short message e.g. `"Addressed in latest commit"`
3. Re-review the updated code and submit a fresh review

## Gotchas

- `create_pr_review` auto-submits when `event` is provided — only use `submit_pr_review` for reviews created in pending state (no event)
- `path` in inline comments must be relative to repo root: `agent/tools/foo.py` not `/repo/agent/tools/foo.py`
- `line` refers to the line number in the **new file** (RIGHT side). Use `side: "LEFT"` for deleted lines.
- You can only dismiss **your own** reviews — not reviews from other users
- `update_pr_review` only updates the review body — it does not update inline comments
- GitHub requires a non-empty `body` for APPROVE and REQUEST_CHANGES events
- `list_pr_review_comments` without a `review_id` returns all review comments on the PR
