---
name: migrate
description: Bring a person's local coding-agent setup into Robin — past Claude Code and Codex conversations become Robin threads, their standing preferences become personal Robin instructions, and their skills become personal Robin skills. Read this whenever someone asks to import, migrate or move over their sessions, conversations, history, memories, CLAUDE.md, AGENTS.md or skills.
---

# Migrate to Robin

Everything to migrate lives on the person's machine, so this needs a run on it: a thread started from the Robin desktop app or the `robin` CLI.
`upload_session` is offered only there.
If it is not among your tools, tell the person to start a thread from the desktop app or CLI and ask again there; do not try to reach their machine another way.

## 1. Take stock, then agree a scope

Look before importing, and report what you found in a few lines: counts per agent, date range and the projects (working directories) involved.

- **Claude Code sessions:** `~/.claude/projects/<project>/<session id>.jsonl`.
  Only files directly in a project folder are sessions; skip anything under a `subagents/` folder.
- **Codex sessions:** `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`.
  The first line is `session_meta`, whose `payload.id` is the session id and `payload.cwd` its project.
  A resumed session writes a new rollout that replays the old one: keep only the newest rollout of each session id.
- **Memories and instructions:** `~/.claude/CLAUDE.md`, `~/.claude/projects/*/memory/*.md`, `~/.codex/AGENTS.md`, and any memory files under `~/.codex/`.
  A repository's own `CLAUDE.md` or `AGENTS.md` stays in the repository, where Robin already reads it.
- **Skills:** `~/.claude/skills/*/SKILL.md` and `~/.codex/skills/*/SKILL.md`.

Ask which sessions to import (everything, some projects, or a recent window) before uploading more than a handful.
Skip sessions with no prompt the person typed.

## 2. Import sessions

Import in bulk: call `upload_session` once per agent with `type` (`claude` or `codex`) and no `repo`, `branch` or `pr_url`, then run the one loop its `next_step` gives over every agreed transcript of that type, on the machine.
Never print an `upload_token`.
Imports are private by default; pass `visibility="workspace"` only if the person asks.

A session that was already imported is skipped, so rerunning a migration adds only what is missing.
A 422 means the file held no conversation; note it and move on.
If the token expires partway, call `upload_session` again and rerun the loop.
Retry other failures once, then report them by path.

## 3. Carry over memories, instructions and skills

Read the memory and instruction files and pick out the standing preferences that are personal to the person and apply to all their work ("always …", "never …").
Leave out one-off task details, facts about a particular repository or team, secrets and credentials, and say what you left out and why.
Show the person the list and save only what they approve with `save_user_instructions`.
It replaces their instructions whole, so keep the `standing_instructions` in their `person` block and add the approved rules to them.

For each skill the person wants, save it with `save_user_skill`, keeping its name, description and instructions; mention any scripts or files beside its `SKILL.md` that did not come along.

## 4. Report

Say how many sessions were imported per agent, which failed, whether their instructions were updated, how many skills were saved, and that the imported threads are in the sidebar.
