# The pull request page

Design for `/agents/reviews/$owner/$repo/$number`, rewritten from scratch.
Background research with citations: [pr-page-research.md](pr-page-research.md).

## What GitHub's page actually is (studied live on langchain-ai/open-swe#3727)

### Chrome
- **Global header** (64px): repo breadcrumb, search, global actions. **Repo nav** (40px): Code / Pull requests (115) / Agents / Actions / …
- **Title block**: 32px title + muted `#3727` + pencil. Right: an "Awaiting approval" status button with a coloured dot, and a "Code" dropdown.
- **Meta line**: green **Open** pill · "Author wants to merge 22 commits into `main` from `branch`" + copy button. The branch names are mono chips in a blue tint.
- **Tabs**: Conversation 19 · Commits 22 · Checks 26 · Files changed 38. A diffstat (+748 −185 plus five tiny blocks) sits right-aligned on the tab rule.
- **On scroll** the title collapses into a sticky 60px bar: pill, title, #n, meta line. The tabs scroll away.

### Conversation tab
- Two columns: a ~820px timeline and a ~320px metadata sidebar (Reviewers + "At least 1 approving review is required", Assignees, Labels, Projects, Development, Notifications, participants, Lock, Archive). Each sidebar section has a gear that opens a picker.
- **The timeline is a vertical rail.** Items hang off it with small round 32px icons: commit (dot), review (eye), push. Comments are full cards with a 40px avatar outside the rail.
  - **Comment cards**: header strip with author, "commented 2 days ago", "edited ▾", role badge (Member / Contributor / Author), ⋯. Markdown body, then an emoji-reaction button.
  - **Reviews**: an event row ("open-swe Bot reviewed yesterday", right-aligned "View reviewed changes"), then the review body card. Its inline threads follow as their own cards: the file path header with an "Outdated" badge, a 4-line diff excerpt, comments with replies, "Reply…" input, and **Resolve conversation**. Resolved threads collapse to a one-line path row with "Show resolved".
  - **Commits**: compact rows (avatar, mono message truncated, ⋯, status ✓/✗, short SHA), grouped under "added 2 commits".
  - **State events**: "marked this pull request as ready for review".
- **Bot noise is real.** On #3727 the Open SWE bot posted the same "No issues found" review five times. Each costs ~250px. GitHub gives them full weight.
- **Merge box** (bottom): a deployment card, then a stack of rows with a big status icon each: Review required ✗, All checks have passed ✓ (expands), branch out-of-date ⚠ (Update branch ▾), Merging is blocked ⚠. The footer button is "Enable auto-merge (squash)". "Still in progress? Convert to draft" sits below.
- **Comment box**: Write/Preview tabs, a markdown toolbar, and an attachments row. Actions are Close pull request and Comment.

### Files changed tab (React rewrite; URL is now `/changes`)
- **Toolbar**: tree toggle, "All commits ▾", progress "0 / 38 viewed", **Submit review ▾** (green), settings gear, info, comments count (5), Copilot.
- **Left**: a resizable tree (~560px) with a filter box and a filter menu. Folder rows; file icons tinted by status (added files get a green "+" file icon).
- **Right**: one continuous scroll of file cards. The sticky file header holds the collapse caret, the path (left-truncated with "…"), copy, a "expand all lines" icon, +/− with five blocks, Copilot, a **Viewed** checkbox, a comment icon, and ⋯.
- **Diff lines**: a 20px row with two number columns. Changed lines carry a solid gutter tint, and changed spans get a stronger inline tint. Hunk headers are a blue band with expand-up/down arrows.
- **Hover** on a line shows a blue **+** button on the gutter edge plus a ▾ menu at the far right.
- Even the sticky page header carries "0/38 viewed", "Awaiting approval" and "Submit review".
- **Cost**: the 38-file page is 40,230px tall with **24,020 DOM nodes**.

### Checks tab
- Left: a list of workflows, each expandable, with an annotation-count badge.
- Right: "Select a check to view from the sidebar". The head commit message and SHA ▾ sit at the top.

## What Devin Review does (same PR, `devinreview.com/<owner>/<repo>/pull/<n>`)
- **Three columns, one scroll.**
  - **Left**: PR # with "sections | folder" view switch, and a file tree with +/− per file.
  - **Centre**: state pill, `owner/repo · #n`, title, author, `base ← head` chips, opened time, file count, ±. Then Description / Discussion 31 / Commits 22 tabs (for the overview only). Then **Changes** (with "0/38", "933 lines left" and diff settings) holds every file below in the same scroll.
  - **Right**: **Info | Chat** tabs. Info shows the analysis (a live tool trace while running), Checks 26/26 as a progress bar, Reviewers, Assignees and Labels.
- Chat is a tab you have to find. Its empty state has three suggested prompts (Fix bugs, Summarize changes, Explain architecture) and an "Ask anything about this PR…" input.
- Context expansion uses "↕ All 20 lines", "↑ 5 lines" and "↓ 5 lines" rows. The comment `+` sits on the line number.

## What we keep, and what we change

Essence taken from GitHub:
- The visual grammar people already read without thinking:
  - state pill colours
  - `base ← head` branch chips
  - mono paths
  - green and red line tints
  - sticky file headers with **Viewed**
  - the merge box as stacked status rows
  - thread cards with Reply and Resolve
- The page answers "can this merge?" without a click.

Where we break from GitHub, because our users have an agent in the room:
1. **The code is never behind a tab.** Users punished Cursor Review for hiding the diff behind a tab (see research). Our centre column is one scroll: status, description, then every file.
2. **One sentence says where the PR stands, and one button does the next thing.** GitHub spreads this over a merge box at the bottom, a status button at the top and a sidebar. We say it once, at the top. This is the page's one bold element.
3. **The agent is in the room.** Chat is a full-height column, open by default, not a tab behind "Info". Every object you can point at offers **Ask** (findings, threads, failing checks, line selections). Agent work started anywhere on the page ("Fix checks", "Address comments") shows up in that same chat, because it is the same thread.
4. **Humans speak; bots whisper.** In the discussion, bot reviews and comments collapse to one line each, and repeats of the same bot verdict fold together. Humans stay expanded.
5. **AI findings are not comments.** They render as margin notes with a severity rule and the agent's mark. Human threads keep the familiar comment-card look.
6. **Nothing loads before it's needed; whatever you are about to need is already loading.**

## Layout

```
┌───────────────────────────────────────────────────────────────────────────────────────┐
│ ← Reviews  ● Open  Show approval state and drop review page polling  #3727            │
│            ramon-langchain · main ← ramonn/polling-…  ⧉  · +748 −185 · 38 files       │
│                                              [12/38 viewed ▮▮▮▯]  [Review ▾ 2]  [⋯]   │
├───────────────┬─────────────────────────────────────────────────┬─────────────────────┤
│ Files │ Guide │ Waiting on review. Checks pass; Open SWE flagged │ Chat  Discussion 31 │
│ ▾ openswe     │ 2 bugs.                         [Review changes] │ ─────────────────── │
│   ▾ github    │ ✓ 26 checks passed                            ▸ │ Open SWE            │
│     • status… │ ✗ 1 approving review required   [Request ▾]     │ …transcript…        │
│     ✓ key.py  │ ● 2 bugs · 3 flags from Open SWE (risk 4/5)   ▾ │                     │
│   ▸ review    │   ● Stale head SHA in …    status.py:88   Ask  ↗ │                     │
│ ▸ tests       │   ● …                                           │                     │
│               │ ─ Description ─────────────────────────────────  │                     │
│               │ Stacked on #3736 …            (clamped, ▾ more)  │                     │
│               │ ─ Changes  38 files · 933 lines left  [⚙] ────── │                     │
│               │ ┌ openswe/github/pull_request_status.py  +98 −31 ☐┐│ [Ask about this PR…]│
│               │ │ 13 − from pydantic import …                     ││ ⌘;                  │
└───────────────┴─────────────────────────────────────────────────┴─────────────────────┘
```

- **Header** (sticky, 2 lines, ~64px): back, state pill, title (semibold, never wraps past one line), `#n`. Meta line: author, branch chips, copy branch, ±, file count. Right side:
  - viewed progress (also a button: jump to the next unviewed file)
  - **Review** (pending-comment count; opens the submit popover)
  - ⋯ (Open on GitHub, Copy link, Re-run Open SWE review, Request review in Slack, Mark ready / Convert, Close PR)
- **Left: Navigator** (240px, `F` toggles, collapsed below 1280px):
  - **Files**: a tree with viewed checks, per-file markers (finding dot by severity, thread dot) and +/−. Filter box.
  - **Guide**: the walkthrough steps, shown when a walkthrough exists or can be built.
  - Clicking scrolls the centre. The current file is highlighted from the scroll position.
- **Centre: one `CodeView` scroll.**
  - `renderCodeViewHeader` holds the **Standing** block, the description and the Changes toolbar.
  - Items are file diffs; in Guide mode, a step's slice of a file.
  - Sticky custom file headers.
- **Right: Rail** (420px, resizable 340–640, `⌘;` focuses chat, `Z` hides the rail and navigator for focus mode).
  - Tabs: **Chat** (default) and **Discussion** (count).
  - Below 1100px the rail is a slide-over opened by a "Chat" button in the header.

### Standing block (the bold element)
- **Line 1**: one plain sentence, built from facts, such as:
  - "Ready to merge."
  - "Waiting on a review. Checks pass; Open SWE flagged 2 bugs."
  - "Checks are failing."
  - "Merge conflicts with main."
  - "Merged by @x 3 days ago."

  The single next-step button sits beside it: Merge / Review changes / Fix with Open SWE / Update branch.
- **Rows**, only for what matters, each with a status mark, a short label and an inline action:
  - **Checks**: counts by state, expandable list with failing first. A failing row offers "Fix with Open SWE"; each failing check offers "Ask".
  - **Reviews**: decision and requested reviewers. Offers "Request in Slack".
  - **Branch**: out of date → Update branch; conflicts → Fix with Open SWE.
  - **Open SWE review**: risk n/5, decision and counts, expandable into the **findings queue**. Each finding has a severity rule, title, `file:line` (click → scroll the diff and open it), **Ask** and **Fix**. Also: re-review, running progress and feedback.
  - **Merge**: method select and button, when mergeable.
- When everything is green, the rows collapse into the sentence and a single "26 checks · approved by @x" line.

### Diff annotations (in order of visual weight)
1. **Human and bot review threads** (GitHub comments): a card with avatars, replies, Reply and Resolve. Resolved and outdated threads collapse to one line. Open SWE's own comments are skipped here because their findings render instead.
2. **Open SWE findings**: a margin note with a 2px severity rule, the agent mark, title, and an expandable body plus suggestion, with Ask / Fix / Copy.
3. **Your pending comments**: a "Pending" card with Edit and Delete.
4. **Chat-drafted comments**: the proposal card, editable and synced with the chat.
5. **The composer**: opened from the gutter `+` or by dragging line numbers.

Selecting code text shows a two-button popover: **Comment** (`c`) and **Ask Open SWE** (`⌘L`).

## Performance plan
- **Query split** (each region paints when its data lands):
  - `review` (shell: PR, checks, findings, walkthrough), small
  - `diff` (patches)
  - `conversation` (timeline + threads)
  - `pr-status` (mergeability)
  - `pending-review`
  - chat thread (stream)
- **Hover intent**: the route `loader` prefetches only `review` (no await, no side effects). The TanStack router already preloads on intent. We never prefetch `…/chat`, because it creates a thread.
- **On mount**: `review`, `diff`, `pending-review` and `conversation` run in parallel. The chat column is visible, so it mounts at once.
- **Code split**: the diff engine (`@pierre/diffs` CodeView, worker pool, Shiki) and the tree load as separate chunks. Their imports start when the route loader runs, so they download in parallel with data.
- **Highlighting** runs off-thread in the existing worker pool; text paints first.
- **On-demand reads**, prefetched on hover or focus of the control that needs them:
  - merge methods (merge button)
  - label catalog (labels picker)
  - human-review availability (⋯ menu)
  - file contents (context expansion, fetched by Pierre)
- **No remount on push**: a new head SHA updates items in place (CodeView `version`) instead of remounting the page. Viewed marks are kept per head SHA.
- **Cache**: query `gcTime` keeps the last PRs warm for back/forward.

## Shortcuts (`?` lists them)

| Key | Action |
|---|---|
| `j` / `k` | next / previous file |
| `n` / `p` | next / previous finding or open thread |
| `v` | toggle viewed, then go to the next unviewed file |
| `F` | toggle navigator |
| `Z` | focus mode (hide navigator and rail) |
| `⌘;` | focus the chat composer |
| `⌘L` | ask Open SWE about the selected lines |
| `c` | comment on the selected lines |
| `⇧D` | split ↔ unified |
| `?` | shortcut help |

## Data changes
- `GET /reviews/{o}/{r}/{n}/conversation` gains:
  - `threads`: inline review threads grouped by reply chain, with resolved/outdated state and node ids
  - `commit` timeline items
- New:
  - `POST …/threads/{comment_id}/replies` (reply on GitHub as the viewer)
  - `POST …/threads/{thread_id}/resolution` (resolve or unresolve as the viewer)
