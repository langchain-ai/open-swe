# Operator Guide

`local_fix_agent.py` is a repair-focused CLI. It takes a reproducible command, gathers targeted context, edits conservatively, reruns validation, and reports either a successful fix or an explicit blocked state.

## Core Modes

- Normal repair: run the agent against a failing command.
- `--publish`: publish the last validated repair result.
- `--publish-only`: publish the current repo state without running the repair loop.
- `--explain-only`: show resolved settings and artifact locations without running the loop.
- `--interactive`: launch the top-level interactive terminal app.

## Quick Commands

```bash
fixit pytest tests/test_x.py -q
fixit --dry-run pytest tests/test_x.py -q
fixit --target edge-01 --repo /srv/app "pytest -q"
./scripts/install_launchers.sh
python local_fix_agent.py --last
python local_fix_agent.py --continue
python local_fix_agent.py --from-last-failure
python local_fix_agent.py --reuse-last-test --repo /path/to/repo
python local_fix_agent.py --last --explain-only
python local_fix_agent.py --interactive
AI_PUBLISH_ALLOW_FORK=1 python local_fix_agent.py --last --publish --publish-pr
AI_PUBLISH_ALLOW_FORK=1 python local_fix_agent.py --publish-only --publish-pr
AI_PUBLISH_ALLOW_FORK=1 python local_fix_agent.py --last --publish --publish-pr --publish-merge
AI_PUBLISH_ALLOW_FORK=1 python local_fix_agent.py --last --publish --publish-pr --publish-merge --publish-merge-local-main
./scripts/fixpublish.sh
./scripts/publishcurrent.sh
```

## CLI Reference

### Context and execution

- `--interactive`
- `--repo`
- `--target`
- `--test-cmd`
- positional test command support
- `--mode`
- `--last`
- `--continue`
- `--from-last-failure`
- `--reuse-last-test`
- `--dry-run`
- `--explain-only`
- `--show-diff`

### Publish and docs

- `--publish`
- `--publish-only`
- `--publish-branch`
- `--publish-pr`
- `--publish-merge`
- `--publish-merge-local-main`
- `--publish-message`
- `--update-docs`

### Advanced/operator controls

- `--http-proxy`
- `--https-proxy`
- `--api-budget-run`
- `--api-budget-attempt`
- `--config`
- `--max-steps`
- `--max-file-chars`

## Run Modes

- `quick`: tighter, faster loop for narrow failures
- `safe`: default operator mode
- `deep`: broader diagnosis and escalation path
- `benchmark`: comparative/stress-style runs

## Wrappers

- `scripts/fixpublish.sh`
  - changes to repo root
  - exports `AI_PUBLISH_ALLOW_FORK=1`
  - runs `python local_fix_agent.py --last --publish --publish-pr`
- `scripts/publishcurrent.sh`
  - changes to repo root
  - exports `AI_PUBLISH_ALLOW_FORK=1`
  - runs `python local_fix_agent.py --publish-only --publish-pr`

## Interactive App

Use `python local_fix_agent.py --interactive` when you want a guided front-end instead of assembling flags by hand.

The interactive mode is now the shared top-level app shell for the whole agent. The menu skeleton and backend routing stay shared across the system, and the main guided workflows now include fixing, creating new scripts, publishing, and training import.

The interactive app has two operator modes:

- `guided`
  - asks the normal workflow questions
  - best when you want to review decisions before running
- `quick`
  - uses the default answers for the common path
  - best when you want the fastest safe route with a confirmation step

You can change the default mode in `Settings / advanced options`.

The top-level menu covers:

- fix or validate a script
- create a new script
- publish current repo state
- publish last validated run
- import a script into training
- inspect learned patterns
- manage patterns
- probe API / M3U8 endpoint
- sync/repair repo conflicts
- settings / advanced options

For each workflow the app shows a short "when to use this" description, gathers the needed inputs, prints a confirmation summary plus the equivalent underlying command, and then lets the operator choose run, back, or cancel.

Typical interactive run shape:

1. `When to use this`
2. minimal prompts
3. confirmation summary
4. command preview
5. `Run`, `Back`, or `Cancel`
6. result block
7. `what_happened`

### Fix or validate a script

This is the primary interactive path for day-to-day work.

Use it when you want to:

- repair a script and rerun validation
- validate a script without entering the repair loop
- choose between auto-detected, remembered, or custom validation commands
- opt into learned pattern usage without assembling flags by hand
- optionally probe a live endpoint when the script looks network-dependent

The guided flow asks for:

- repo path
- script path
- mode: `fix and validate` or `validate only`
- validation command choice: auto-detect, use remembered default, or custom
- learned pattern source: auto, default repo, none, or specific repo/path
- advanced options only when requested

If the script appears network-dependent, the workflow can optionally offer a bounded API or M3U8 probe before execution. Probing remains optional and only supports this workflow; it does not replace it.

The result screen summarizes:

- `validation_result`
- script path
- validation command used
- patterns applied or pattern-selection context when available
- whether probing was used
- publish/finalization status when the fix flow reached it
- `blocked_reason` when the run blocks
- a short plain-English `what_happened` summary

Blocked runs now also include a minimal `next_step` when the agent can suggest one safely.

### Create a new script

Use this when you want the agent to build a new script from an idea, validate it, optionally repair it, and then optionally publish it.

The workflow asks for:

- repo path
- script purpose
- output path
- script domain hint when guided mode is used
- pattern source: auto, default, none, or a specific repo/path when guided mode is used
- validation plan: auto-detect, syntax-only, CLI help, or custom command
- optional bounded API or M3U8 probing when the task appears network-dependent
- optional repair-on-failure and publish-after-success behavior in advanced mode

The generation pipeline now separates and combines:

- task intent
- trusted learned patterns from file-level and repo-level sources
- optional live probe findings
- a selected validation plan

The result screen summarizes:

- `script_generated`
- output path
- generation confidence
- pattern source used
- patterns applied
- whether probing was used
- key probe findings
- validation result
- validation plan
- repair attempt/result when relevant
- publish result and PR URL when publish was chosen
- a plain-English `what_happened` summary

Generation confidence is strongest when trusted patterns match the task, probe findings reduce network ambiguity, and the tool can select a concrete validation command. It drops when the task is vague or network behavior remains unverified.

### Publish current repo state

Use this when you want to publish the current working tree through the canonical finalizer path, but you want a guided preflight before it runs.

The workflow asks for:

- repo path
- publish mode: normal or force publish
- whether safe files should be auto-staged
- whether validation should be refreshed automatically when needed
- advanced options only when requested

Before confirmation it shows:

- changed publishable files
- staged vs unstaged state
- whether a validation record exists
- whether the current commit still matches that validation
- whether revalidation is likely
- whether publish would block and why
- the staging plan
- the equivalent backend command

Auto-stage behavior stays backend-driven:

- safe `code`, `docs`, `tests`, `scripts`, and relevant `config` files can be staged automatically
- ignored internal state files stay out of the publish set
- high-confidence temporary artifacts can be removed automatically so publish can continue
- unsafe or ambiguous files still block and are shown explicitly

Blocker remediation stays narrow and auditable:

- known temporary artifact files are removed by default when the classifier is confident they are junk output
- internal state files remain non-blocking and are never published
- broad ignore rules are not added automatically unless policy explicitly allows them
- ambiguous code, config, JSON, or unknown data files still require manual review

Disable this behavior with `--no-auto-remediate-blockers`.

If you need repo-specific policy, configure `publish_blockers` in the agent config with:

- `auto_remove_safe_artifacts`
- `auto_ignore_known_junk`
- `known_junk_globs`
- `safe_ignore_globs`
- `safe_remove_globs`

Blocked publish means the finalizer found a real safety issue such as:

- unstaged or ambiguous files remain
- validation is missing or stale and could not be refreshed safely
- a branch, push, or mergeability safety check failed

When publish blocks on unstaged or ambiguous files, the tool now prints a staging block analysis for each blocker:

- path
- file type
- classification source
- whether the file is publishable
- confidence
- exact blocking reason
- recommended action
- exact commands to stage, ignore, unstage, or remove the file safely

Typical recommendations are:

- `stage and include in publish`
- `leave untracked / do not publish`
- `remove generated artifact`
- `inspect manually before staging`

The result screen also gives:

- `next_step_primary`
- `next_step_fallback`
- `rerun`

When remediation runs successfully, the publish result also shows:

- `blocker_remediation_attempted`
- `blocker_remediation_result`
- `auto_removed_paths`
- `remaining_true_blockers`

Interactive mode can additionally:

- show the file analysis again
- stage only the files already classified as safe publishable candidates
- show ignore/remove suggestions

The result screen reports:

- `validation_result`
- `publish_triggered`
- `publish_result`
- `pr_url`
- branch used
- mergeability result
- `blocked_reason` when relevant
- a short `what_happened` summary

### Import a script into training

Use this when you want to ingest a real script into the private pattern repo through a guided safety path instead of pushing files into training manually.

The workflow asks for:

- source type: local file, SSH path, or HTTP/HTTPS URL
- source location
- training repo target: auto/default, existing repo, or create new repo
- pattern tags and an optional pattern-type hint
- trust level: `trusted` or `experimental`
- whether to sanitize, validate/repair, and allow auto-fix before promotion

The interactive precheck then handles:

- source acquisition metadata such as `source_type`, `source_origin`, `acquisition_method`, and `proxy_used`
- sanitization, including redaction of secrets and environment-specific values while preserving structure
- validation and optional repair before promotion
- pattern classification, applicability context, and confidence level
- trust safety, including automatic recommendation of `experimental` when confidence or validation is too weak for trusted promotion

Trusted vs experimental:

- `trusted`
  - used by default in future learned runs
  - only allowed when the imported script validates cleanly and is safe for promotion
- `experimental`
  - weaker influence
  - used when the script is useful to retain but not safe enough for trusted promotion

If trusted promotion is unsafe, the workflow blocks trusted promotion and offers an explicit experimental fallback instead of silently accepting risky content.

The result screen summarizes:

- `import_success`
- target repo path
- trust level applied
- learned pattern count change when available
- `validation_result`
- `repair_result`
- warnings
- a short `what_happened` summary

### Import a repo or folder into training

Use `--import-pattern-repo <path>` when the useful conventions live across a collection instead of a single file.

This mode scans a local repo or folder, keeps obvious junk out by default, and imports matching scripts through the same per-file safety pipeline:

- sanitize
- validate
- repair when possible
- promote to `candidate`, `curated_experimental`, or `curated_trusted`

Default behavior:

- includes `*.py`
- supports `--pattern-include` and `--pattern-exclude`
- supports `--pattern-max-files` and `--pattern-max-depth`
- preserves file-level provenance such as source root, subpath, trust, validation result, and sanitization status

Collection import also learns repo-level conventions such as:

- directory and script layout
- repeated helper structure
- naming conventions
- dominant validation style
- shared network/proxy/retry/config patterns when they recur across the collection

Stored collection imports are grouped under `imports/...` inside the private pattern repo so related sources stay together. After import, the tool rebuilds pattern memory and reports:

- `import_scope`
- `candidate_count`
- `promoted_trusted_count`
- `promoted_experimental_count`
- `blocked_count`
- `repo_level_patterns_added`
- `pattern_memory_delta`

## Global Launchers

Install user-level launchers with:

```bash
./scripts/install_launchers.sh
```

By default this installs into `~/.local/bin`:

- `fixapp`
  - launches `python local_fix_agent.py --interactive`
- `fixpublish`
  - launches the canonical finalizer `./scripts/fixpublish.sh`
- `fixit`
  - launches `python local_fix_agent.py ...`

The installer keeps the repo-based engine intact. Each launcher resolves the repo in this order:

- `OPEN_SWE_REPO` if set
- `LOCAL_FIX_AGENT_REPO` if set
- the current working directory, if it looks like a compatible repo
- the default repo path baked in at install time

You can override the install location with `--bin-dir` and the baked-in default repo with `--repo`.

If the chosen bin directory is not on `PATH`, the installer prints exact commands like:

```bash
export PATH="$HOME/.local/bin:$PATH"
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

## Publish Workflows

### Validated-run publish

Use `--publish` after a successful repair run. The tool publishes only the validated repair change set and blocks if unrelated working tree changes would be included.

### Publish-current workflow

Use `--publish-only` to stage and publish the current repo state. This mode is for operator-driven publishing when you already know what should be committed.

By default the finalizer/publish workflow will auto-stage safe publishable files before blocking:

- tracked or new publishable `code`, `docs`, `tests`, `scripts`, and behavior-relevant `config` paths can be staged automatically
- ignored internal state files such as `.ai_publish_state.json` stay out of the publish commit
- each encountered path is classified as `code`, `test`, `docs`, `config`, `script`, `generated`, `state`, `artifact`, or `unknown`
- publishability is derived from that classification plus the existing ignore rules, so the tool can explain whether a file was auto-staged, ignored, or blocked
- the tool re-audits the working tree after auto-stage and only continues when publishable files are truly staged
- ambiguous or unsafe files still block with exact `git add -- <path>` handoff commands

Use `--no-auto-stage` to disable automatic staging. In that mode the workflow preserves strict staging checks and blocks with exact manual commands instead.

Use `--explain-staging` to print the full per-file classification and action list in the publish/finalization summary.

### Live API and M3U8 probing

Use `--probe-url <url>` when a script depends on live endpoint behavior and the agent needs evidence instead of guessing.

- supported probe modes: `head`, `get`, `json_summary`, `headers_summary`, `m3u8_summary`, or `auto`
- `auto` chooses `m3u8_summary` for `.m3u8` URLs and `json_summary` otherwise
- probes are bounded by timeout, max bytes, and a small follow-up limit for variant or segment checks
- probes inherit `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY`, or accept `--http-proxy` / `--https-proxy`
- request customization is available via `--probe-header`, `--probe-bearer-token`, `--probe-cookie`, `--probe-user-agent`, and `--probe-method`
- probe output redacts auth headers, cookies, credential-bearing URLs, and obvious token/query-secret fields
- script validation discovery may suggest a live probe when the script looks API- or HLS-dependent

Probe result fields include:

- `probe_type`
- `endpoint`
- `status_code`
- `content_type`
- `redirected`
- `proxy_used`
- `proxy_likely_worked`
- `summary`
- `redactions_applied`
- `probe_confidence`

`m3u8_summary` additionally reports:

- `valid_playlist`
- `playlist_type`
- `variant_count`
- `sample_variant_uris`
- `audio_group_references`
- `subtitle_group_references`
- `target_duration`
- `media_sequence`
- `segment_count`
- `segment_sample_count`
- `sample_segment_uris`
- `key_tags_present`
- `uri_reference_mode`
- `sample_uri_probe_results`

### PR and merge behavior

- `--publish-pr` creates or reuses a PR with GitHub CLI.
- `--publish-merge` creates or reuses the PR and then attempts a safe auto-merge.
- `--publish-merge-local-main` checks out `main` and pulls `origin main` after a successful auto-merge.
- Auto-merge is only allowed for self-owned fork PRs targeting `main`.
- Auto-merge uses squash merge and does not delete branches.

### Publish summary fields

- `resolved_target`
- `control_path`
- `state_loaded`
- `state_reset`
- `reused_fork`
- `transport_locked`
- `state_confidence`
- `pr_already_exists`
- `pr_merge_attempted`
- `pr_merge_success`
- `pr_merge_block_reason`
- `merged_pr_url`
- `local_main_synced`
- `final_status`
- `reason`
- `next_action`
- `auto_stage_attempted`
- `auto_stage_result`
- `auto_staged_paths`
- `remaining_unstaged_paths`
- `remaining_unstaged`
- `file_decisions`
- `staging_summary`
- `staging_decision_reason`
- `staging_reason`

## Blocked and Control-Path Semantics

Current control paths include:

- `blocked_missing_origin`
- `blocked_auth`
- `fork_push`
- `direct_origin_push`
- `noop`

Blocked summaries are explicit operator messages, not silent retries.

## Docs Drift Summary

The tool tracks documentation drift with:

- `docs_check_performed`
- `docs_status`
- `docs_reason`
- `docs_required`
- `docs_updated`
- `docs_targets`
- `docs_refresh_mode`

`docs_refresh_mode` is one of `none`, `patch`, or `rewrite`.
`docs_status` is one of `up_to_date`, `updated`, or `required_but_blocked`.
