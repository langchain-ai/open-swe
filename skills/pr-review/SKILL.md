---
name: pr-review
description: Use when asked to review a pull request, leave feedback on a PR, check code quality, or request changes, or when @openswe is mentioned in a PR comment asking for a review.
---

# PR Review Skill

## Goal

Leave a structured GitHub review — not just a plain comment. Use the review tools to create inline feedback or request changes directly on the PR.

**Do not approve PRs.** If you think a PR looks good and should be approved, leave a `COMMENT` review stating that it looks good and should be approved. Actual approval must be done by a human.

## Review Process

**Before starting:** Extract the PR number from the PR URL in your prompt (e.g. `https://github.com/owner/repo/pull/123` → `123`). You will need this for every tool call.

1. Call `list_pr_reviews` first — see what's already been reviewed so you don't duplicate feedback
2. Determine the PR's base branch from the PR context (e.g. the webhook payload or PR URL metadata), then run `git diff origin/<base_branch>...HEAD` in the sandbox to get the PR diff — the repo is already cloned and checked out to the PR branch
3. Read the changed files in the sandbox — the repo is already checked out, no need to clone
4. **Completeness check (MANDATORY — do this for EVERY changed file BEFORE writing the review):**
   - For each changed file, use `grep` or `execute` to search the **entire** file — do NOT rely on `read_file` alone since it truncates large files.
   - For each new name in the diff, pick a **sibling** (an existing name next to it) and `grep` the changed files for that sibling. Every place the sibling appears, the new name should too. If it doesn't, flag it.
   - For every new line added, compare it against its immediate neighbors. If adjacent lines share a common pattern (prefix, structure, naming convention) and the new line breaks that pattern, flag it.
   - Check if there are other functions, handlers, or code paths in the same file that work with the same data but were NOT updated.
   - Grep the repo for other callers or consumers of any changed interface — flag anything that still uses the old name, old endpoint, or old behavior.
   - **Do NOT skip this step for ANY file — including small or "obvious" changes.** The smallest changes are the most likely to hide bugs. Do NOT submit the review until you have completed all completeness checks on every changed file.
5. **Deleted code audit (MANDATORY if any files or functions were deleted):**
   - For every file fully deleted in the diff, run `git show HEAD^:<path>` to read its contents before deletion.
   - For every function or helper removed from an existing file, read the `-` lines in the diff to get its full implementation.
   - List each behavior the deleted code provided. Then verify each behavior is still present somewhere in the new code. If any behavior was lost, flag it.
   - **Do NOT skip this step.** Checking "no dangling references" is not enough — you must verify "no lost behaviors."
6. Create the review using `create_pr_review` with inline comments where possible
7. Always call `github_comment` after submitting the review with a short human-readable summary
   - If no critical or high severity issues were found, post: `"🤖 PR Review — No critical or high severity issues found."`
   - If issues were found, summarize them briefly

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

**Must check — incomplete changes:**

After reviewing what changed, ask: **"what else should have changed but didn't?"**

- For every new name introduced in the diff, find its **siblings** — other names that follow the same pattern or live alongside it. Then **grep the changed files for a sibling** (not the new name). Every place a sibling appears, the new name should probably appear too. If it doesn't, flag it. This is the most important check — do not skip it.
- For every change in the diff, grep for other places in the repo that depend on the same name, interface, or behavior — and flag any that weren't updated.

**This is the highest-value part of the review.** Most bugs that reach production are not in the code that was written — they're in the code that should have been changed but wasn't. Always run the sibling grep before concluding a review.

**Must check — old-vs-new audit (MANDATORY — do this BEFORE writing the review):**

For every deleted or replaced block in the diff, you MUST:

1. **List every behavior the old code provided.** For every deleted function, helper, or middleware, read its full implementation from the diff output (the `-` lines) — not just its name. If a file was fully deleted, run `git show HEAD^:<path>` or `git diff` to see its contents. You MUST read the body of deleted code to understand what it did. Then write out each thing the old code did — every case it handled, every path it covered, every invariant it maintained.
2. **Verify each behavior is preserved in the new code.** Go through your list one by one. For each old behavior, find where the new code handles it. If you cannot find a match, flag it.
3. **Check existing state.** For every new condition, filter, or column the code depends on: grep for how existing data/callers would interact with it. If existing rows, callers, or consumers would break or become invisible, flag it.
4. **Trace every path: create, read, update, AND delete.** Don't stop at "does the new code work for normal operations?" For every new relationship, reference, or dependency introduced, ask: "what happens when the referenced thing is removed?" If a FK, cascade rule, or dependency exists, trace the delete path and flag any unintended side effects.

**Do NOT skip this step. Do NOT submit the review until you have completed this audit.** If the old code covered N cases and the new code covers N-1, that is a bug until the author confirms otherwise. Never assume asymmetry is intentional. Never rationalize a gap — flag it and let the author respond.

**Critical: your job is to find risks, not prove correctness.** If the old code covered N cases and the new code covers fewer, you MUST flag it as a potential regression in your review — even if you believe the narrowing is correct. Do NOT use words like "correct narrowing", "intentional", "makes sense", or "good" to describe a scope reduction. Instead, flag it: "The old code covered X, the new code only covers Y — is this intentional?" Let the author justify the narrowing. Your job is to surface the change, not to judge whether it's acceptable.

**Skip entirely:**
- Style preferences not enforced by a linter
- Subjective refactors outside the PR scope
- Minor formatting (let CI/linters handle it)

## Review Events — When to Use Each

- **REQUEST_CHANGES** — there are blocking issues the author must fix before merge.
- **COMMENT** — feedback only, not blocking. Use for questions, suggestions, or when the PR looks good and should be approved.

Never use APPROVE. Never REQUEST_CHANGES for style nits.

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
event: str                # REQUEST_CHANGES | COMMENT (APPROVE is not allowed)
body: str                 # Top-level review summary (required for REQUEST_CHANGES)
comments: list            # Optional inline comments (see format below)
commit_id: str            # Optional — defaults to latest commit
```

Inline comment format:
```json
{
  "path": "src/utils/auth.py",
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

## Sensitive Path Scrutiny

After getting the diff, check if any changed files match these patterns.
If they do, apply the stricter criteria below — regardless of how small the change looks.

| Path pattern | What to check |
|---|---|
| `**/auth/**`, `**/authn/**`, `**/authz/**` | Auth bypass, privilege escalation, token handling, missing permission checks |
| `**/migrations/**`, `**/alembic/**` | Destructive SQL (DROP, DELETE without WHERE), missing rollback, column type changes on large tables, lock-level of each DDL statement on existing tables |
| `.github/workflows/**` | Secrets being printed/exported, untrusted input in `run:` steps, pinned action SHAs changed |
| `**/.env*`, `**/secrets/**` | Hardcoded credentials being added, secrets committed to source |
| `**/middleware/**` | Auth middleware bypassed or reordered, new routes skipping auth |

For any file matching the above: always use `REQUEST_CHANGES` if something looks off — don't downgrade to `COMMENT`.

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
- GitHub requires a non-empty `body` for REQUEST_CHANGES events
- `list_pr_review_comments` without a `review_id` returns all review comments on the PR
