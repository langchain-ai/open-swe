---
name: executing-plans
description: >-
  Use when you have a written implementation plan to execute. Loads the plan,
  reviews it critically, executes all tasks with verification checkpoints, and
  reports when complete. Enforces scope lock — only do what the plan says.
user-invocable: false
---

# Executing Plans

## Overview

Load plan, review critically, execute all tasks, report when complete.

**Announce at start:** "Executing plan — reviewing before starting."

## STOP — Ask Before Acting

**NEVER start executing immediately.** Present the plan review and your understanding to the developer. Wait for their go-ahead before making any changes.

## The Process

### Step 1: Load and Review Plan

1. Read the plan file or plan provided by the developer
2. Review critically — identify any questions, gaps, or concerns
3. If concerns: Raise them with the developer **before** starting implementation
4. If no concerns: Confirm understanding and proceed

### Step 2: Execute Tasks

For each task in the plan:

1. Announce which task you're starting
2. Follow each step exactly — the plan has bite-sized steps for a reason
3. Run verifications as specified in the plan (lint, type checks, manual review)
4. Confirm task completion before moving to the next one

**Stay in scope:** Only implement what the plan specifies. Do not refactor, clean up, or "improve" anything outside the plan.

### Step 3: Lint and Verify

After all tasks are complete:

1. `ruff check --fix <changed_files>` — auto-fix lint issues
2. `ruff check <changed_files>` — verify no remaining issues
3. `ruff format <changed_files>` — format code
4. Fix any issues surfaced by the PostToolUse hook (`python_quality.sh`)

### Step 4: Report Completion

After all tasks complete and verified:

1. Summarize what was implemented (list of files changed/created)
2. Suggest a branch name: `yogesh/<short-description>`
3. Suggest a commit message: Conventional Commits style (e.g., `feat(tools): add web search tool`)
4. **CRITICAL: Do NOT create branches, commit, or push without explicit developer permission**

## When to Stop and Ask

**STOP executing immediately when:**
- Hit a blocker (missing dependency, test fails, instruction unclear)
- Plan has critical gaps that prevent proceeding
- You don't understand an instruction
- Verification fails repeatedly
- Something outside the plan needs changing to make it work

**Ask for clarification rather than guessing.**

## When to Revisit

**Return to Review (Step 1) when:**
- Developer updates the plan based on your feedback
- Fundamental approach needs rethinking after hitting a blocker

**Don't force through blockers** — stop and ask.

## Rules

- Review plan critically before writing any code
- Follow plan steps exactly as written
- Don't skip verifications
- Stop when blocked — never guess
- Never commit, push, or create branches without developer permission
- Never start implementation on main/master branch without explicit consent
- All code must be production-ready (no hardcoded values, no debug leftovers, proper error handling, structured logging)
