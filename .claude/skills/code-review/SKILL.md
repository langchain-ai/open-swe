---
name: code-review
description: >-
  Review code changes for production readiness. Use when asked to review a PR,
  look at a diff, check someone's changes, or give feedback on code before merging.
  Compares implementation against requirements, categorizes issues by severity,
  and gives a clear merge verdict.
user-invocable: false
---

# Code Review

You are reviewing code changes for production readiness.

## Step 1: Gather Context

1. Read the PR description, linked ticket, or task context
2. Understand **what** was implemented and **why**
3. Get the diff:
   ```bash
   git diff --stat <base>..<head>
   git diff <base>..<head>
   ```

## Step 2: Review Checklist

**Code Quality:**
- Clean separation of concerns?
- Proper error handling? (tools return `{success, error}` dicts)
- Type safety — annotations on all function signatures?
- DRY — no duplicated logic that should live in `agent/utils/`?
- Edge cases handled? (None, empty inputs, missing config)

**Architecture:**
- Sound design decisions?
- Follows open-swe conventions? (sync tools, `get_config()`, `create_deep_agent`)
- Max 3 positional args per function?
- Reusable code in `agent/utils/`, not inline in tools?

**Security:**
- No hardcoded tokens, API keys, or secrets?
- `shlex.quote()` on all dynamic values in sandbox `execute()` calls?
- Tokens via encrypted config or env vars only?

**Testing:**
- Tests actually test logic (not just mocks)?
- Edge cases covered?
- All tests passing?

**Requirements:**
- All ticket requirements met?
- Implementation matches spec?
- No scope creep — only what was asked for?

## Step 3: Output

### Strengths
[What's well done? Be specific with file:line references.]

### Issues

#### Critical (Must Fix)
[Bugs, security issues, data loss risks, broken functionality]

#### Important (Should Fix)
[Architecture problems, missing error handling, test gaps]

#### Minor (Nice to Have)
[Style, optimization, documentation]

**For each issue:**
- `file:line` reference
- What's wrong
- Why it matters
- How to fix (if not obvious)

### Assessment

**Ready to merge?** [Yes / No / With fixes]

**Reasoning:** [1-2 sentence technical assessment]

## Rules

**DO:**
- Categorize by actual severity — not everything is Critical
- Be specific — `file:line`, not vague
- Explain **why** issues matter
- Acknowledge strengths
- Give a clear verdict
- Use the **logic-check** skill for deep edge case analysis when needed

**DON'T:**
- Say "looks good" without actually checking the diff
- Mark nitpicks as Critical
- Give feedback on code you didn't read
- Be vague ("improve error handling" — say what and where)
- Suggest refactors outside the PR scope
- Flag style issues that ruff/black handle automatically
