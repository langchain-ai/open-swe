---
name: unnecessary-tests-check
description: Audit a PR or branch diff for bloat without edits. Flag unnecessary tests and trimmable, collapsible, or unrelated hunks with estimated LOC savings. Run alongside the core Open SWE review workflow.
---

# unnecessary-tests-check
DO NOT USE PLAN MODE
DO NOT USE SUBAGENTS
DO NOT EDIT ANY FILES

Audit-only counterpart of `deslop`. Read a target PR or branch, find every
place the diff is larger than it needs to be, and hand back a report. The user
decides what to act on; you change nothing.

## Prime directive

The smallest diff that fully delivers the stated change wins. Every added line
must justify its existence. Report anything that cannot.

Never recommend trading correctness for line count. Do not flag error
handling, cleanup, type hints, docstrings required by repo convention, or
behavior the PR description promises.

---

## Pass 1 — tests

**Default stance: a test is unnecessary until proven otherwise.**

Necessary means at least one of:

1. **Mechanical adaptation** — an *existing* test must change so the suite still
   compiles/passes after an API or type change (fixture/import updates).
2. **Sole failing guard** — without this test, a plausible regression would
   still leave CI green *and* typecheck/lint green.

Flag as unnecessary:

- Net-new regression tests that re-prove a type/contract the production code
  already enforces
- Tests that call the production helper to build the fixture, then only assert
  that helper's own output round-trips
- Duplicates of coverage already provided by older tests in the same area
- Speculative tests for unshipped behavior, scaffolding, or "for later"
- Parity/exhaustiveness tests that do not lock a user-visible failure mode

Flag as collapsible when several cases *are* warranted: near-duplicate bodies
that belong in one `parametrize`, test-only helper frameworks/fixtures/factories
that do not replace real duplication.

Call it out explicitly if tests are the largest part of the diff.

## Pass 2 — implementation

Ask of every added hunk: *would the PR still work if this were gone?* If yes,
flag it. Then ask: *can what remains be expressed in fewer lines without
getting clever?* If yes, flag it with the shorter shape.

Flag for removal:

- Unused parameters, flags, config knobs, exports, and constants with one caller
- Abstractions with a single implementation or single call site
- Compatibility shims, deprecation paths, and back-compat branches for code that
  never shipped
- Defensive checks for states the type system or callers already preclude
- Redundant validation repeated at multiple layers
- Speculative extension points ("in case we need…"), TODO scaffolding, dead
  branches, commented-out code
- Logging/telemetry/comments added incidentally, not requested
- Wrapper functions that only forward arguments
- Restated docs: comments that narrate the line below them
- Reformatting, reordering, renames, and drive-by refactors unrelated to the
  change

Flag for collapse:

- New function/class/module where extending an existing one would do; a new
  file is the largest possible diff shape
- New helpers used once that belong inline in their caller
- Parallel utilities where an existing repo utility already exists — grep to
  confirm before flagging
- Hand-rolled loops/branches the stdlib or an existing repo primitive replaces
  more clearly
- Nested conditionals that could merge or early-return
- Intermediate variables used once and named no better than the expression
- Hand-written config/schema/docs entries that are generated or derivable

Flag blast radius:

- Files touched that the intent does not require
- Public API signature changes where an internal change suffices
- Renumbered, re-sorted, or re-wrapped untouched neighboring code

---

## Scope

- **In scope:** everything the PR/branch adds or modifies — tests, production
  code, config, docs added by the PR
- **Out of scope:** pre-existing code the PR does not touch, missing tests,
  missing features, correctness bugs, style opinions. Mention a correctness bug
  in one line if you happen to see one, but it is not a finding of this review.

## Workflow

1. **Establish the diff**
   - PR number or URL: `gh pr view <n> --json title,body,baseRefName` then
     `gh pr diff <n>` (or fetch the branch and diff against its base, using
     `git diff --stat` for the summary)
   - Branch: `git diff origin/main...HEAD` plus `git diff --stat origin/main...HEAD`
   - Uncommitted only: `git diff` / `git diff --staged`
   - If the base is unclear, ask once

2. **Read the intent** — PR description/issue/commit messages. Anything in the
   diff not traceable to that intent is a finding.

3. **Read the surrounding code, not just the diff.** A helper "used once" may
   have an existing sibling; a "new" utility may duplicate one that already
   exists. Grep before flagging.

4. **Classify every hunk**
   - Tests: `adapt` / `new` / `already-deleted`
   - Production: `required` / `trim` / `collapse` / `unrelated`

5. **Write the report** (format below). Every finding must name the file and
   line range, say what to do, and estimate lines saved.

6. **Stop.** No edits, no commits, no branches. If the user wants the findings
   applied, they will ask for `deslop`.

## Report format

Lead with the verdict, then the tables. Keep prose to a minimum.

```
## Unnecessary tests check: <PR title or branch>

**Verdict:** <one sentence: how much is trimmable and where it concentrates>

| Diff | Files | +LOC | -LOC | Est. removable |
|---|---|---|---|---|
| tests | | | | |
| production | | | | |
| total | | | | |

### Tests

| Location | Kind | Keep? | Why | Saves |
|---|---|---|---|---|
| tests/foo/test_bar.py:12-48 | new | no | re-proves the type contract already enforced by `Bar.__init__` | ~36 |

### Implementation

| Location | Kind | Keep? | Why | Saves |
|---|---|---|---|---|
| agent/foo.py:10-30 | collapse | partial | `_build_x` has one caller; inline it | ~12 |

### Unrelated changes

| Location | What | Saves |
|---|---|---|

### Kept as required
<one line per hunk that is clearly justified — brief, so the user sees the whole diff was considered>
```

Order findings within each table by lines saved, largest first. `Keep?` is
`yes` / `no` / `partial`. Quote the PR promise a hunk fulfils when keeping
something that looks removable.

## Anti-patterns

- Do not edit, stage, commit, or push anything
- Do not pad the report with praise or general observations
- Do not flag behavior the PR description promises
- Do not flag pre-existing code, lint nits, or missing coverage
- Do not recommend golfing: dense one-liners, dropped type hints, stripped
  required docstrings, swallowed errors
- Do not list a hunk as removable without reading enough of the codebase to be
  sure nothing else depends on it
- Do not skip the "kept as required" section — an audit that only lists
  removals cannot be checked for completeness
