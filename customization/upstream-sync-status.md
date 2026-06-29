# Upstream Sync Status

Tracks the sync state between our fork (`local/self-hosted`) and `langchain-ai/open-swe`.

Update this file every time you run the daily workflow from `open-swe-daytona-setup.md`.

---

## Latest status — 2026-06-29

| Item | Value |
|---|---|
| **Our `main` tip** | `6d125526` — hotfix: stop prompting agent/reviewer to wrap installs in sfw (#1625) |
| **Upstream HEAD** | `6d125526` — hotfix: stop prompting agent/reviewer to wrap installs in sfw (#1625) |
| **Commits behind** | **0** |
| **Rebase needed** | No |

Sync completed 2026-06-29. `main` fast-forwarded 17 commits, `local/self-hosted` rebased cleanly with one conflict in `agent/server.py` (resolved by keeping `logger.info` + `return` at end of Daytona block) and one stash conflict in `CLAUDE.md` (resolved by merging `ensure_no_empty_msg` and `SanitizeThinkingBlocksMiddleware` into the middleware list).

### All upstream commits since last sync

```
6d125526 hotfix: stop prompting agent/reviewer to wrap installs in sfw (#1625)
5da3d0c6 fix: post reviewer resolution notes verbatim (#1624)
5dc360d8 chore(deps): bump langgraph-checkpoint from 4.1.0 to 4.1.1 (#1619)
8c944381 feat: restore forced tool call to prevent premature run stops (#1622)
209132d3 refactor: durable interrupt dispatch + completion webhook (#1621)
85c0f63e feat: clickable shared PR header in git panel and reviews (#1620)
83cb40a0 chore: update langsmith sdk to 0.9.3 (#1616)
baf0c248 feat: add filter & grouping menu to agents threads sidebar (#1617)
2f56d754 fix: omit plan link in PR description when no plan exists (#1618)
f29868ff feat: recover thread work as patch (#1615)
29015fad feat: gate workflow pushes with approval (#1614)
e5a29eca feat: include plan links in PR descriptions (#1613)
69148f54 feat: add PR trace resolution (#1612)
48bf712b feat: show message timestamps (#1609)
00906401 feat: editable plan mode + fix review-plan banner overlap (#1610)
c3292d82 chore: bake sfw binary into sandbox image (#1611)
f6c215ff feat: add automation template gallery (#1608)
```

---

## How to sync upstream — full step-by-step

Run these steps in order every time upstream has new commits. Execute one step, check output, then proceed.

---

### Step 0 — Stash any uncommitted WIP

```bash
git stash push -m "wip before upstream rebase"
git stash list   # confirm it appears
```

Skip if working tree is clean (`git status` shows nothing modified).

---

### Step 1 — Fetch upstream

```bash
git fetch upstream
```

No output = already up to date. Branch refs updated = new commits available.

---

### Step 1b — Check what's new (optional but recommended)

```bash
# How many commits behind are we?
git log main..upstream/main --oneline | wc -l

# Which of our customized files did upstream touch?
git log main..upstream/main --oneline -- \
  agent/server.py \
  agent/tools/web_search.py \
  agent/utils/langsmith.py \
  agent/integrations/daytona.py \
  pyproject.toml \
  Makefile
```

If the second command lists any files, read the diffs carefully before rebasing — those files will conflict.

---

### Step 2 — Fast-forward `main` to upstream

```bash
git checkout main
git merge upstream/main --ff-only
```

Expected: `Fast-forward` message. If it says "Not possible to fast-forward" — stop; you have stray commits on `main`. Investigate before continuing.

---

### Step 3 — Push updated `main` to your fork

```bash
git push origin main
```

---

### Step 4 — Rebase your customizations onto the new `main`

```bash
git checkout local/self-hosted
git rebase main
```

Git replays your custom commits on top of the 17 new upstream commits. It pauses on each conflict.

---

### Step 5 — Resolve conflicts (if any)

For each conflicted file git reports:

```bash
git status                        # see which files are conflicted
# open the file, find <<<<<<< / ======= / >>>>>>> markers, edit to resolve
git add <resolved-file>
git rebase --continue             # git may open an editor for the commit message — just save and exit
```

If something goes badly wrong:
```bash
git rebase --abort                # restores you to before the rebase started
```

**Known conflict patterns for this setup:**

| File | What to do |
|---|---|
| `agent/server.py` | Keep your Daytona `elif` blocks intact; accept upstream's surrounding structural changes |
| `agent/utils/langsmith.py` | Minor whitespace/log-level changes from upstream — accept them and keep our null-guard |
| `agent/tools/web_search.py` | Keep our SearXNG version entirely — discard upstream's Exa changes |
| `pyproject.toml` | Accept upstream's version bumps AND keep the `exa-py` removal |
| `Makefile` | Keep our tracing-disabled `dev` target |

**`agent/server.py` specific:** the conflict is typically at the end of the Daytona `elif` block in `_refresh_github_proxy()`. Keep `logger.info(...)` from our commit and `return` from upstream; discard any ghost LangSmith code that appeared on the HEAD side (it's already handled inside the `if sandbox_type == "langsmith":` block above).

---

### Step 6 — Restore stashed WIP

```bash
git stash pop
```

If the stash conflicts with the rebased code, resolve the same way as Step 5 then:
```bash
git add <resolved-file>
git stash drop                    # clear the stash once fully resolved
```

---

### Step 7 — Push the rebased branch to your fork

```bash
git push origin local/self-hosted --force-with-lease
```

`--force-with-lease` refuses if someone else pushed to the branch since your last fetch — safer than `--force`.

---

### Step 7b — Commit and push any remaining uncommitted changes

After stash pop, `git status` may show staged files (resolved conflicts, WIP) and new untracked files (e.g. `customization/upstream-sync-status.md`). Commit and push them as a normal (non-force) push:

```bash
git status                        # see what's staged / untracked

# Stage everything you want to include
git add <file1> <file2> ...       # list explicitly — avoid git add -A

git commit -m "chore: post-rebase WIP + <short description>"

git push origin local/self-hosted # no --force-with-lease needed — this is a new commit on top
```

> **Why no force here?** The rebase force-push already rewrote the branch. This subsequent commit is a normal fast-forward — `--force-with-lease` is not needed and could mask mistakes.

---

### Step 8 — Verify

```bash
# main mirrors upstream exactly
git log main -1 --oneline
git log upstream/main -1 --oneline
# both should show the same commit hash

# your custom commits are still on top
git log main..local/self-hosted --oneline
# should list your 2 commits (new hashes after rebase, same messages)

# nothing left to pull
git log main..upstream/main --oneline | wc -l
# should print 0
```

---

### Step 9 — Update this file

Update the "Latest status" section and add a row to the History table below with today's date, the new tip, and what conflicts (if any) you resolved.

---

## History

| Date | Our `main` tip | Upstream HEAD | Commits behind | Action taken |
|---|---|---|---|---|
| 2026-06-29 | `6d125526` (#1625) | `6d125526` (#1625) | 0 | Synced — 1 conflict in `agent/server.py`, 1 stash conflict in `CLAUDE.md` |