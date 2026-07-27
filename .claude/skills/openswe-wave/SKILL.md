---
name: openswe-wave
description: Operate an Open SWE delivery wave with full-weight plan adjudication and low-noise mechanical monitoring. Use for wave dispatch, plan approval, spot-audit, review follow-up, closeout, recorded-event replay, the two documented merge-queue recoveries, source-anchor checks, and LangSmith trace summaries.
---

# Open SWE wave operations

Keep plan adjudication and spot-audits at full operator weight. Use these files only to remove mechanical polling, status relay, and deterministic recovery work.

## Deployment

This skill deploys as a git checkout, never as copied files. Clone the repo once per
machine, move any existing copied install aside (`ln -sfn` will not replace a real
directory — it creates the link inside it and the stale copy stays active), then link:

```bash
dest=~/.claude/skills/openswe-wave
[ -d "$dest" ] && [ ! -L "$dest" ] && mv "$dest" "$dest.pre-checkout"
ln -sfn <checkout>/.claude/skills/openswe-wave "$dest"
```

(and the same into `${CODEX_HOME:-$HOME/.codex}/skills`.) Upgrade with
`git -C <checkout> pull` — plain `git pull` from the target repository checkout the
setup below has you working in would pull the wrong repo. Answer "what is this
machine running" with `git -C <checkout> rev-parse HEAD`; detect drift with
`git -C <checkout> status`. Do not hand-copy files into the skill directories —
copies are exactly how the installed docs went stale for a day (dogfood log,
2026-07-26).

## Required setup

Run from the target repository checkout. Live commands require the named environment variables below and fail with an export instruction when one is absent.

```bash
export GH_TOKEN=dummy
export LINEAR_API_KEY=...
export LANGGRAPH_URL=https://...
export LANGSMITH_API_KEY=...
```

`GH_TOKEN=dummy` is correct inside an Open SWE sandbox because the GitHub proxy injects the installation token. Outside that environment, set a token accepted by `gh`.

Live paths require the Python modules `httpx` and `langgraph_sdk`. The imports are lazy: `httpx` gates Linear GraphQL calls, while `langgraph_sdk` gates LangGraph thread and run reads. Fixture and replay paths (`replay --fixture`, `recover --fixture`, and `trace-digest --fixture`) and `--help` do not require either module.

On studio2, run live commands with the control-plane interpreter at `/opt/mobilyze/open-swe-control-plane/current/.venv/bin/python`. On another machine, use `uv` with `--no-project` so it does not resolve the target checkout before adding the live-path dependencies:

```bash
uv run --no-project --with httpx --with langgraph-sdk python \
  .claude/skills/openswe-wave/scripts/wave-monitor watch \
  --issue-id <linear-uuid> --repo <owner/repo> --pr-number <number>
```

## Workflow

1. Use `scripts/anchor-sweep <ref> <ticket-file>` before dispatch. Treat present/moved/missing as mechanical evidence only; inspect semantic drift yourself.
2. Use the templates in `references/comment-templates.md` for dispatch, approval, spot-audit, closeout, and the OSWE-100 tally.
3. Apply `references/adjudication-checklist.md` before approving a plan.
4. Start the quiet monitor after dispatch:

```bash
/opt/mobilyze/open-swe-control-plane/current/.venv/bin/python \
  .claude/skills/openswe-wave/scripts/wave-monitor watch \
  --issue-id <linear-uuid> --repo <owner/repo> --pr-number <number>
```

The first sample is a silent baseline. The only emitted wake nodes are:

- `plan_posted`
- `review_findings_posted`
- `run_blocked`
- `terminal_merged`
- `terminal_closed`
- `terminal_run_error`
- `unhandled_condition`

PR creation, acknowledgements, normal progress, successful recoveries, queue entry/position changes, and comments authored by the Linear viewer identity stay quiet. Pass `--session-user-id` only when viewer discovery is unavailable.

5. Follow `references/recovery-runbook.md`. The watch command begins before PR creation and discovers the PR from LangGraph metadata. It defaults to recovery dry-run output; after reviewing the recorded-state exercises, restart it with `--apply` to enable acting recovery.
6. Use `scripts/trace-digest <thread>` for status, token, error, recent-activity, and prompt-size rollups.
7. Complete the spot-audit and closeout templates. Confirm the tracker transition rather than assuming it.

## Status cross-check

Run this on every operator contact and at deadline boundaries to cross-check sibling progress without extending or resetting any delivery deadline:

```bash
scripts/wave-monitor status-sweep --repo owner/repo --tickets tickets.json
```

`--tickets` is exactly one non-empty JSON list of objects with `identifier`, Linear UUID `issue_id`, and optional non-empty `thread_id`; no other keys or duplicate identifiers, issue IDs, or thread IDs are accepted. Omitted thread IDs are derived from the Linear issue UUID. `--divergence-minutes` defaults to 15.

The command performs one repository-wide `gh pr list --state all` read and one concurrent LangGraph thread/run/plan-store read per ticket. It writes one compact, input-ordered JSON line per ticket with `identifier`, `issue_id`, `thread_id`, `lifecycle_stage`, `stage_at`, `pr_number`, `pr_state`, `thread_status`, `errors`, and `sibling_divergence`; divergence evidence names the leading sibling, stage and timestamp, elapsed lag, and threshold. Missing, malformed, ambiguous, wrong-repository, or unavailable PR evidence leaves lifecycle stage and timestamp indeterminate and is excluded from sibling divergence rather than inferred from plan evidence. Thread metadata PR numbers are trusted first and, when metadata repository fields are present, only when they match `--repo`. Lifecycle precedence is `merged`, `closed`, `pr-open`, `approved`, `planned`, `dispatched`; merged and closed are terminal peers for sibling divergence.

## Replay and diagnostics

```bash
scripts/wave-monitor replay --fixture tests/skills/fixtures/openswe_wave/oswe-79-events.json --max-wakes 6
scripts/wave-monitor recover --fixture tests/skills/fixtures/openswe_wave/pr-43-green-draft.json
scripts/wave-monitor recover --fixture tests/skills/fixtures/openswe_wave/pr-44-queue-stall.json
scripts/trace-digest <thread> --fixture <recorded-runs.json>
```

The monitor is disposable when OSWE-106 replaces session-side liveness polling. The templates, adjudication checklist, recovery evidence gates, anchor sweep, and trace digest remain useful operator assets. Never wire this skill into the deployed service or modify product auto-merge behavior from here.
