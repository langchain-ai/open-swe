# Corridor gaps

A dataset of security findings Corridor (`corridor-security[bot]`) reported on `langchain-ai/open-swe` PRs, labelled with whether the Open SWE reviewer (`open-swe[bot]`) found the same issue, on which commit, and what humans concluded. Use it to measure and improve the reviewer's recall on security issues.

Every finding is pinned to the exact commit Corridor reviewed, not the PR's final head, so intermediate versions can be replayed.

## Pipeline

```bash
uv run python -m evals.corridor_gaps.collect --since 2026-06-29   # GitHub → .cache/prs/*.json
uv run python -m evals.corridor_gaps.judge                         # LLM labels → .cache/judgements/*.json
uv run python -m evals.corridor_gaps.build                         # → data/
```

`judge` calls `claude-opus-5-5` with `ANTHROPIC_API_KEY`. Without a working key, `judge --emit-prompts` writes the rendered prompts and `schema.json` to `.cache/prompts/` for any external judge, and `judge --check` validates the resulting `.cache/judgements/*.json`. All three steps skip cached PRs; pass `--refresh` to redo them.

## Outputs

### `data/findings.jsonl`

One row per top-level Corridor inline comment.

| Field | Meaning |
|---|---|
| `id` | `langchain-ai/open-swe#<pr>/<corridor comment id>` |
| `pr` | `number`, `title`, `url`, `author`, `created_at`, `state` (`open`/`closed`/`merged`), `base_ref`, `head_ref`, `final_head_sha`, `commit_count` |
| `commit.head_sha` | The commit Corridor reviewed |
| `commit.base_sha` | Merge base of `head_sha` with `base_ref`, so `base_sha...head_sha` is the diff Corridor saw |
| `commit.index` | 1-based position in the PR's current commit list; null when `force_pushed_away` |
| `commit.is_final_head` | Whether this is the PR's last head |
| `location` | `path`, `start_line`, `line`, `side`, `diff_hunk` from the comment |
| `corridor` | `comment_id`, `url`, `created_at`, `finding_id`, `finding_url`, `review_summary`, `body` (boilerplate stripped) |
| `label.issue_key` | Kebab-case slug shared by re-posts of the same root cause within a PR |
| `label.is_first_occurrence` | True on the earliest comment for each `issue_key`; count distinct issues with this |
| `label.title`, `label.golden_comment` | Reviewer-style title and self-contained 1–3 sentence statement of the defect |
| `label.category` | `prompt_injection`, `authorization`, `credential_misuse`, `secret_exposure`, `data_exposure`, `ci_supply_chain`, `command_injection`, `resource_exhaustion`, `web_security`, `ssrf`, `data_integrity`, `other` |
| `label.cwe`, `label.severity` | CWE ID or null; judge-assessed `critical`/`high`/`medium`/`low` |
| `label.detection_hint` | The reusable review heuristic that would have caught it |
| `triage.verdict` | From human replies: `valid_fixed`, `valid_accepted_risk`, `valid_unresolved`, `false_positive`, `disputed`, `no_response` |
| `triage.evidence`, `triage.fix_commit`, `triage.replies` | Deciding reply, fix SHA if named, full reply thread |
| `oswe.detection` | See below |
| `oswe.match_reasoning` | Why the judge matched or did not, including near misses |
| `oswe.reviewed_corridor_commit`, `reviews_before/at/after` | Open SWE review coverage relative to the Corridor commit |
| `oswe.matched` | The Open SWE comments judged to be the same issue, with commit, severity, title, body |
| `oswe.reviews` | Every Open SWE review on the PR: commit, outcome, finding count, risk, trace URL |

`oswe.detection`:

- `caught_same_commit`: Open SWE raised it on the commit Corridor reviewed.
- `caught_earlier_commit` / `caught_later_commit`: Open SWE raised it on a different commit only.
- `missed_on_same_commit`: Open SWE reviewed the Corridor commit and did not raise it.
- `missed_other_commits_only`: Open SWE reviewed the PR, never that commit, and never raised it.
- `not_reviewed`: Open SWE never reviewed the PR.

### `data/examples.jsonl`

Reviewer-eval examples in the `evals/reviewer` shape (`inputs`, `outputs.golden_comments`, `metadata`), one per PR × Corridor commit. Each golden comment is the first occurrence of an issue that humans did not mark as a false positive. `golden_comments[].oswe_detection` lets you score recall on historical misses separately from issues the reviewer already finds.

### `data/summary.md`

Counts by detection, category, triage, and severity, plus the list of misses on PRs Open SWE reviewed.

## Caveats

- Only PRs Corridor reviewed are included, so this measures security recall, not overall reviewer quality.
- Labels come from an LLM judge reading both bots' comments; they are not human-verified. `triage.verdict` is the only human signal, and many findings have no reply.
- Detection is judged against what Open SWE posted. A finding Open SWE's agent considered and dropped counts as missed.
