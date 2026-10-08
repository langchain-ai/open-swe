# PR page research: what the best review UIs do

Research for redesigning Open SWE's pull-request review page (GitHub PR data, an AI reviewer's findings, and a prominent agent chat). Every claim links to its source. Research date: October 2026.

Our current stack already includes `@pierre/diffs` 1.3.6 (uses `Virtualizer`, `FileDiff`, `WorkerPoolContextProvider`; **`CodeView` ships in the installed package but is unused**), TanStack Router with `defaultPreload: "intent"` and `defaultPreloadStaleTime: 0`, TanStack Query, TanStack Virtual, and Shiki 4. The recommendations at the end build on that stack.

---

## 1. Graphite (now part of Cursor)

**Status.** Cursor agreed to acquire Graphite on Dec 19, 2025. Graphite keeps operating with the same team and product ([SiliconANGLE](https://siliconangle.com/2025/12/19/cursor-acquires-ai-code-review-startup-graphite/), [Cursor blog](https://cursor.com/blog/graphite)). The review surface is now also called "Cursor Review" ([Cursor forum](https://forum.cursor.com/t/the-updated-pr-ui-is-a-huge-step-back/166402)).

### PR page layout
- **Stack** at the top lists sibling PRs in the stack ([PR page overview](https://graphite.com/docs/pr-page-overview)). Press `S` to show the stack and jump between PRs ([review docs](https://graphite.com/docs/review-pull-requests)).
- **Description** supports rich text or Markdown. `/` inserts images, tables, collapsibles, and alerts, and a **Generate** button writes the description with Graphite Agent ([PR page overview](https://graphite.com/docs/pr-page-overview)).
- **Discussion** sits below the description and holds PR-level comments ([PR page overview](https://graphite.com/docs/pr-page-overview)).
- **Status panel (top right)** shows status, review status (including "issues Graphite Agent found"), expandable checks, reviewers (hover to remove or re-request), labels, assignees, linked Linear tasks, and an auto-detected **Preview** deployment link ([PR page overview](https://graphite.com/docs/pr-page-overview)).
- **File tree + diff** sit below description and discussion. `F` toggles the tree; when collapsed, a table of contents appears on the left. `linguist-generated` in `.gitattributes` hides generated files by default ([PR page overview](https://graphite.com/docs/pr-page-overview)).
- The Nov 2025 redesign moved the file tree to the left, put **versions above the diff**, consolidated CI into one panel, and added **Action Cards** that surface the next step ("View checks", "Merge 2 PRs") as one-click actions ([changelog summary](https://www.plushcap.com/content/graphite/blog/graphite-graphite-changelog-11-20-2025), [PR page feature page](https://graphite.com/features/pr-page)).
- The marketing page emphasizes "a continuous view" for reading files ([PR page](https://graphite.com/features/pr-page)).
- **Right tray** defaults to a timeline of changes, comments, and reviews. Clicking a line comment in the tray scrolls to the code. Clicking the selected tab dismisses the tray, which enters **Focus Mode** ([review docs](https://graphite.com/docs/review-pull-requests)).
- **Versions:** compare any two versions of the PR. Older docs mention `V` to toggle versions; newer docs use a left/right version dropdown ([versions docs](https://graphite.com/docs/pull-request-versions)).

### Commenting and review flow
- Comment on any line, changed or not. Hover a line number and click, or drag for a range ([PR page overview](https://graphite.com/docs/pr-page-overview)).
- The editor opens **to the right of the diff**. `Cmd+Enter` posts immediately; checking "Add to review" batches the comment, and the batch count shows on a **Finish review** button at the top ([PR page overview](https://graphite.com/docs/pr-page-overview)).
- The line overflow menu offers Add comment, Suggest change, **Add to chat** (opens the chat sidebar with those lines as context), Copy code, and Copy link ([PR page overview](https://graphite.com/docs/pr-page-overview)).
- Two-key review shortcuts: `R C` comment, `R N` request changes, `R A` approve, `R Y` approve without a comment ([PR page overview](https://graphite.com/docs/pr-page-overview)).

### Graphite Agent (formerly Diamond) and chat
- In Oct 2025 the "Diamond" AI reviewer and Graphite Chat merged into one product, **Graphite Agent**, so users "review, edit, and merge without leaving the PR" ([agent announcement](https://graphite.com/blog/introducing-graphite-agent-and-pricing), [Oct 16 changelog](https://www.graphite.com/blog/changelog-october-16)).
- AI findings are posted as **inline comments on the relevant lines**, alongside human reviews ([AI review experimental comments](https://graphite.com/docs/ai-review-experimental-comments)). 👍/👎 reactions are the feedback channel; a 👎 in Graphite opens a popup asking why ([same doc](https://graphite.com/docs/ai-review-experimental-comments)).
- Graphite markets a noisy-or-unhelpful comment rate under 5% ([AI reviewer page](https://graphite.dev/lp/ai-code-reviewer)). A Graphite talk reported a downvote rate under 4%, without a stated denominator ([AI Engineer talk](https://ai.engineer/talks/TswQeKftnaw-ai-powered-entomology-lessons-from-millions-ai)).
- **Chat sits in a right-side panel** on the PR page ([chat docs](https://graphite.com/docs/graphite-chat)). Open it with "Ask Graphite" or **`Cmd+;`** ([chat launch](https://graphite.com/blog/introducing-graphite-chat)).
- You can highlight lines and ask about them. Context includes codebase history, the full stack, CI failures, reviewer comments, and other PRs ("How does this relate to PR #123?") ([chat docs](https://graphite.com/docs/graphite-chat)).
- Proposed edits open in an **Editor** for preview, then are applied as tracked commits. You can then commit and merge without a local checkout ([chat docs](https://graphite.com/docs/graphite-chat), [chat launch](https://graphite.com/blog/introducing-graphite-chat)).
- Chat fixes CI failures and resolves reviewer comments "with one click" ([chat launch](https://graphite.com/blog/introducing-graphite-chat)).

### Speed
- Sep 2025 changelog: "PR pages now load twice as fast" ([changelog summary](https://www.plushcap.com/content/graphite/blog/graphite-graphite-changelog-09-17-2025-1)). No public engineering write-up on how.
- The inbox advertises realtime sync, full keyboard navigation, and at-a-glance statuses ([search summary of graphite.dev/features/inbox](https://graphite.dev/features/inbox)).

### What users value (and punish)
- A July 2026 layout change made Cursor Review "essentially just the Github PR review page." It hid the stack behind a small indicator, put the diff behind a "Changed Files" tab click, and moved comments inline instead of in a right column. The user said they had "essentially no reason to use Cursor Review over Github now." Staff called it unintended and rolled it back ([Cursor forum](https://forum.cursor.com/t/the-updated-pr-ui-is-a-huge-step-back/166402)).
  - **Lesson:** the differentiators users notice are (1) the diff reachable with zero extra clicks, (2) a prominent stack, and (3) comments in a side column instead of interrupting the code.

---

## 2. "giti": what the user probably meant

I found no product named "Giti" or "Gitty" in code review ([search 1](https://alternativeto.net/software/gito/), [search 2](https://extensionauditor.com/scan/gitty-lbelcompobjfpedpgnpfdojjfokgaghf)). Candidates, most to least likely:

1. **Gitea** (pronounced "git-tee"), the closest phonetic match. Its PR "Files changed" view has a commit/range picker (shift-click like GitHub), "Show changes since your last review", and per-file "Viewed" checkboxes that are disabled when viewing a commit subset ([Gitea test source](https://gitea.com/dco/gitea/src/branch/main/tests/integration/pull_diff_test.go)). It is a GitHub clone rather than a source of new ideas. Its one lesson is incremental review ("since your last review").
2. **`gt`**, Graphite's CLI, said aloud as "gee-tee" ([CLI docs](https://graphite.dev/docs/install-the-cli)). If so, this is just Graphite again (Section 1).
3. **Gitar**, Sonar's AI review agent (Sonar acquired it May 2026). It pushes fixes and re-runs CI until green ([Sonar](https://www.sonarsource.com/products/gitar/), [aicodereview.cc](https://aicodereview.cc/tool/gitar)). Its presentation: inline findings plus **one continuously-updated dashboard comment** with verdict, CI analysis, severities, and resolved findings ([chatgate review](https://chatgate.ai/post/gitar)).

**My belief:** phonetically this is **Gitea**. But the user's intent ("feel like GitHub but better, extremely fast") better matches the newer "GitHub-but-faster" review surfaces below, which are more useful to study.

### Pierre / DiffsHub
- Swap `github.com` for `diffshub.com` in a PR URL and the diff opens in Pierre's viewer, with no login ([noqta](https://noqta.tn/en/blog/diffshub-pierre-virtualized-github-diff-viewer-2026)). This is the same `@pierre/diffs` library we already use.
- The **"On Rendering Diffs"** deep dive (May 29, 2026) explains the techniques ([Pierre](https://pierre.computer/writing/on-rendering-diffs)):
  - **Inverse Sticky Technique** for zero-blanking virtualization. Negative sticky `top`/`bottom` equal to `(contentHeight − viewportHeight) × −1` pin the rendered window to the viewport edge when JS lags, so you never see white.
  - **Height estimation** with no DOM: `lineHeight × lines` per file; `lineHeight × splitLineCount + hunks × separatorHeight` per diff. Deltas are corrected incrementally after render.
  - **Model-based scroll anchoring** with `overflow-anchor: none`.
  - **Memory:** copying line strings detaches them from the 700 MB source patch, cutting the Linux v6→v7 diff from 2.4 GB to 1.15 GB with ~80% faster parse. Pooled Shadow DOM containers and shared options state saved another 20–30 MB.
  - **Highlighting:** plain text first, then a **worker pool** (one Shiki highlighter per worker), an LRU cache with a hard size limit, and a **`prime` API to pre-highlight files likely to appear soon**.
  - **Open problems:** paint cost under aggressive scroll, highlight serialization across threads for huge files, and no horizontal virtualization of minified lines.
- `CodeView` renders a **mixed, virtualized list of files and diffs in one scroll container**, with sticky headers, `scrollTo`, and `addItems`/`updateItem`. `@pierre/diffs/ssr` pre-renders highlighted HTML, and annotations are typed per line and side ([diffs.com llms-full](https://diffs.com/llms-full.txt)).

### Stage (YC S26) and Pyor
- **Stage** groups a PR into ordered "chapters." Each chapter notes what changed and what to double-check. The company claims review "up to 5x faster than GitHub" ([YC](https://ycombinator.com/companies/stage)).
- Show HN critics said chapters explain *what* but not *why*. They also warned AI narratives can be "persuasive and self-serving" and could encourage rubber-stamping the un-highlighted parts. Founders responded with plans for linked intent (Linear) and a `CHAPTERS.md` ([HN thread](https://hn.svelte.dev/item/47796818)).
- **Pyor** offers one window for read, comment, approve, and merge. It has a triage file rail, folder-level viewed tracking, focus mode, and AI groups by complexity with **one-line labels instead of long summaries** ([dev.to](https://dev.to/pyor/github-pr-review-alternatives-in-2026-an-honest-comparison-4m0h)).
- Open-source keyboard-first clients: **fast-reviewer** (`r` marks the file viewed *and* advances; `⌘K` PR picker), **GitHub-client** (SQLite cache so screens open instantly and refresh in the background), and **pr-review** (`P` opens the preview deployment) ([search summary](https://github.com/matmeylan/fast-reviewer), [GitHub-client](https://github.com/A-and-Brian/GitHub-client/pull/1), [pr-review](https://github.com/sunwrobert/pr-review)).

---

## 3. GitHub's new PR experience

### New "Files changed" page (React)
- Public preview Jun 2025 ([changelog](https://github.blog/changelog/2025-06-26-improved-pull-request-files-changed-experience-now-in-public-preview/)); default for everyone Jan 22, 2026, with opt-out ([changelog](https://github.blog/changelog/2026-01-22-improved-pull-request-files-changed-page-on-by-default/)).
- **Layout:** resizable file tree on the left with markers for files that have comments, errors, or warnings. Filters apply to both the tree and the diff. Side panels list comments (with text search) and check annotations ([Jun 2025](https://github.blog/changelog/2025-06-26-improved-pull-request-files-changed-experience-now-in-public-preview/)).
- **Comments are minimized by default**; `i` toggles them ([Jun 2025](https://github.blog/changelog/2025-06-26-improved-pull-request-files-changed-experience-now-in-public-preview/)).
- **Draft comments persist locally** across refreshes. You can comment on any line, not only near changes, and pending comments show in the submit panel ([Jan 2026](https://github.blog/changelog/2026-01-22-improved-pull-request-files-changed-page-on-by-default/)).
- An **Overview button** shows the PR description without leaving Files changed ([Nov 2025](https://github.blog/changelog/2025-11-20-pull-request-files-changed-public-preview-november-20-updates/)). Refreshing for new changes and toggling split/unified **no longer reloads the page** ([Jan 2026](https://github.blog/changelog/2026-01-22-improved-pull-request-files-changed-page-on-by-default/)).
- **Large PRs:** single-file mode turns on automatically for big PRs (file limit raised 300 → 1,000) ([Sep 2025](https://github.blog/changelog/2025-09-11-pull-request-files-changed-public-preview-experience-september-11-updates)). An experimental virtualization mode trades away browser find, select-all, print, and extensions ([Jan 2026](https://github.blog/changelog/2026-01-22-improved-pull-request-files-changed-page-on-by-default/)).
- **Viewed:** marking a file Viewed collapses it and counts toward a progress bar. It is **auto-unmarked if the file changes** later ([docs](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/reviewing-changes-in-pull-requests/reviewing-proposed-changes-in-a-pull-request)).

### How GitHub made diff lines fast ([engineering blog](https://github.blog/engineering/architecture-optimization/the-uphill-climb-of-making-diff-lines-performant/))
- **Before:** worst cases had a >1 GB JS heap, >400k DOM nodes, and bad INP. v1 used ~10–15 DOM nodes, 8–13 React components, and 20+ event handlers per line.
- **v2 changes:**
  - dedicated split and unified line components instead of shared wrappers
  - per-line handlers replaced by **one top-level delegated handler reading `data-*` attributes**
  - comment and menu state moved into **conditionally rendered children**
  - **O(1) `Map` lookups** (`commentsMap[path]['L8']`)
  - `useEffect` only at file level, **enforced by lint rule** so line components memoize reliably
- **Result on a 10k-line split diff:** components 183k → 50k, memory ~50% lower, INP ~450 ms → ~100 ms.
- **p95+ PRs** (>10k lines) use window virtualization with **TanStack Virtual**: ~10× less heap and DOM, INP 275–700 ms → 40–80 ms.
- **Also:** avoided `:has()` selectors; GPU transforms for drag/resize; server-side hydration of only visible lines; progressive and background loading; INP segmented by diff size in Datadog.

### Older but still relevant: progressive diff loading ([2016 post](https://github.blog/engineering/architecture-optimization/how-we-made-diff-pages-3x-faster/))
- First request returns **the file list plus stats** (`git diff-tree --raw --numstat`). Patches load per file in batches; the first batch is capped at 400 lines / 20 KB. Over-limit files get a "load diff" button.
- Sizing came from data: 80% of viewed diffs were under 20 KB.
- Result: diff timeouts dropped immediately, high-percentile load improved ~3×, and site-wide p99.9 fell ~3.5 s.

### Code view virtualization lessons ([2023 post](https://github.blog/engineering/architecture-optimization/crafting-a-better-faster-code-view/))
- Virtualizing an 18k-line file cut the initial render from ~27 s to under 1 s.
- **Native Ctrl+F was preserved** with a hidden full-text textarea under an unsearchable highlighted overlay.
- **Highlight data is sent as offset segments, not HTML.** Bypassing React for the overlay cut keyup handling from ~870 ms to ~80 ms.

### Copilot app: rendering huge PRs ([engineering blog](https://github.blog/engineering/user-experience/rendering-huge-pull-requests-in-the-github-copilot-app))
- Target case: 2,200 files, >1M changed lines, >400 comments, with ~100 mounted rows at a time.
- **Rendering:**
  - **imperative recycled renderer** with no React component per row
  - **all code-row heights known before paint**, using typed-array prefix sums
- **Comments with unknown heights:**
  - comments are separate blocks anchored to file, line, and side, so code geometry never rebuilds when a comment resizes
  - cached heights are keyed by **fingerprint** (content + `<details>` state + composer state) and **width bucket**
  - measurement runs only when scrolling settles, within ~2,400 px of the viewport
  - `ResizeObserver` only flags blocks for re-measure and never writes heights
- **Scroll anchoring** corrects only for changes above the viewport and never fights pointer or wheel momentum.
- **Data:**
  - **structure before content** (tree and metadata paint first)
  - **off-main-thread highlighting**, with plain text first
  - **lazy markdown bodies**
  - **retained shell cache of the last few diffs** so revisits repaint instantly while refreshing in the background
- **Perf budgets asserted in E2E tests:** zero late block insertions, commits per frame, and a rAF jank sampler.

### Merge box (GA Mar 2025) ([changelog](https://github.blog/changelog/2025-03-04-improved-pull-request-merge-experience-is-now-generally-available/))
- Checks are grouped by status with **failing first** and sorted in natural order.
- Commit-metadata rule failures are reported **at merge time** so they can be fixed there.
- Supports direct, bypass, auto-merge, and merge queue.
- Keyboard focus management and landmarks.

### PR dashboard / inbox (GA Jul 2026) ([DevOps.com](https://devops.com/githubs-redesigned-pr-inbox-tackles-the-review-bottleneck-ai-created/))
- Inbox sections: needs your review, needs your team's review, needs fixes (CI failing or new comments), ready to merge / in queue.
- `j`/`k` navigation; saved views.
- Agent-created PRs are attributed to the human who directed them.

### Copilot code review + chat on PRs
- **Chat at three scopes:** whole PR (Copilot icon top-right), one file ("Ask about this diff"), or a selected line range (shift-click line numbers → Ask about this diff). The conversation appears **alongside the diff** ([docs](https://docs.github.com/en/enterprise-cloud@latest/copilot/tutorials/explore-pull-requests)).
- **Findings carry High/Medium/Low severity** in the top-right corner, and **similar comments are grouped** so a repeated rename is raised once (May 2026) ([changelog](https://github.blog/changelog/2026-05-12-copilot-code-review-comment-experience-improvements)).
  - Users report severity can be miscalibrated ([morphllm guide](https://www.morphllm.com/copilot-code-review)).
- **Fix with Copilot** opens a dialog: commit to this PR or open a new PR, choose the model, and add instructions.
  - **Fix batch with Copilot** lets you select several findings and hand them off together (May 2026) ([changelog](https://github.blog/changelog/2026-05-19-easily-apply-copilot-code-review-feedback-with-copilot-cloud-agent)).
- **Auto-resolution:** a later commit that addresses a finding resolves it on re-review. Applying a suggestion generates a real commit message. The reviewer now runs builds and tests in a sandbox, giving more high-severity findings and fewer nits (Sep 2026) ([changelog](https://github.blog/changelog/2026-09-11-auto-resolution-and-analysis-updates-in-copilot-code-review)).
- **Copilot app PR view** ([docs](https://docs.github.com/en/copilot/how-tos/github-copilot-app/managing-issues-and-pull-requests)):
  - Overview (summary, checks, review activity) → Files changed tab → "Create session" for an agent tied to the PR
  - a **Fix** button per review comment and **Fix failing checks** at the bottom
  - **Agent Merge** watches CI and reviewers, fixes blockers, and merges once allowed
- **Stacked PRs** (public preview Jul 30, 2026) add a **stack navigator in the PR header** listing PRs in order with the current one highlighted ([gh-stack UI guide](https://github.github.com/gh-stack/guides/ui/), [Better Stack](https://betterstack.com/community/guides/linux/github-stacked/)).

### Shortcuts on GitHub today ([docs](https://docs.github.com/en/get-started/accessibility/keyboard-shortcuts))
- `C` commits dropdown, `T` file filter, `Cmd+Shift+Enter` submit comment, shift-click range, `Q` request reviewer, `L` label, `A` assignee.
- Users note `j`/`k` don't work in Files changed ([refined-github issue](https://oss.issuehunt.io/r/sindresorhus/refined-github/issues/2893)). This is a gap we can beat.

### Community sentiment
- HN threads on GitHub's React migration complain about the site getting "slower and more janky" and about Ctrl+F regressions in virtualized views ([HN mirror](https://hn.svelte.dev/item/44799861)). Any virtualization we do must keep find-in-page working.

---

## 4. GitLab, Gerrit, Phabricator, Reviewable, Critique, Meta

### GitLab
- **MR page redesign (in progress):** **move merge checks to the top of the Overview** so status is visible "without scrolling." Review and merge actions are unified, and comments are managed in a thread panel ([design issue](https://gitlab.com/gitlab-org/gitlab/-/work_items/586835)).
- **Rapid Diffs** ([dev docs](https://docs.gitlab.com/development/fe_guide/rapid_diffs/), [epic](https://gitlab.com/groups/gitlab-org/-/epics/18457)):
  - The **first batch of diff files is server-rendered inline** in the initial HTML; the rest arrive as **streamed HTML**.
  - `<diff-file>` web components with **one delegated click listener** and **one shared IntersectionObserver**.
  - Vue components mount lazily on first interaction.
  - **`content-visibility: auto`** reserves off-screen space using a server-supplied row count.
  - **Startup `fetch()` calls in `<head>`** start before the JS bundles load.
  - **Cookies persist file-browser visibility and width** so SSR renders the right layout with no shift.
  - Target: 1,000+ files without lag (previously lagged after 5–10).
- **Shortcuts** ([docs](https://docs.gitlab.com/user/shortcuts/)):
  - `j`/`]` next file, `k`/`[` previous file
  - `n`/`p` next/previous **open thread**
  - `v` toggle viewed
  - `Cmd+P` file finder, `Shift+F` file browser
  - `;` expand all, `Shift+;` collapse all
  - `Shift+D` inline/side-by-side
  - `c`/`x` next/previous commit
  - `r` reply quoting the selection
  - `Cmd+Enter` adds to the pending review, `Shift+Cmd+Enter` publishes now
- **Duo:** MR-aware chat over the description, threads, diff, and metadata. A dev MR adds chat inside Duo review threads by tagging Duo, with thread notes as context ([blog](https://about.gitlab.com/blog/chat-about-your-merge-request-with-gitlab-duo/), [MR](https://gitlab.com/gitlab-org/gitlab/-/merge_requests/175244/pipelines)).

### Gerrit: the attention set ([docs](https://gerrit-review.googlesource.com/Documentation/user-attention-set.html))
- Explicit **"whose turn is it"**: an arrow icon before a **bolded** name on the dashboard and change page. A hovercard shows why and when the user was added.
- The **"Your turn"** dashboard section shows a "Waiting" duration.
- **Rules:** replying removes you and adds the people you replied to; a reviewer reply adds the owner; a new patch set alone doesn't change it; **bots are never added**.

### Phabricator Differential
- **"Done" checkbox** on inline comments ([T1460](https://secure.phabricator.com/T1460)):
  - lets the author acknowledge a comment without typing
  - reviewers can pre-mark their own comments done to signal "optional"
  - done-marks stay private until the next major action, then publish together

### Reviewable.io
- **File matrix** of every file × revision with drag-to-set diff bounds ([docs](https://docs.reviewable.io/files)):
  - per-user **reviewed marks per revision** (red → green)
  - a "Show Diffs to Review" button jumps to the next unreviewed range
  - strategies like "skip files claimed by others"
- **Discussion dispositions** ([docs](https://docs.reviewable.io/discussions)):
  - Discussing, Blocking, Working, Satisfied, Informing, Pondering
  - a thread resolves when someone is Satisfied and nobody is Blocking or Working
  - keywords set the disposition ("LGTM"/"Done" = satisfied, "Bug"/"Major" = blocking)
  - discussions are grouped into **To reply / Unresolved / Resolved**

### Google Critique ([SWE at Google ch. 19](https://abseil.io/resources/swe-book/html/ch19.html))
- **Principles:** simplicity, **fast loading, hotkey navigation**, clear review-state markers, and linking out to other tools instead of embedding them.
- **Diffs:** intraline, word-aware diffs; whitespace-ignore levels; **move detection**; **shortcuts that jump between modified sections only**.
- **Snapshots:** a compact snapshot chain widget with **prefetched snapshots** so switching is near-instant. Per-file "reviewed" checkboxes clear when a new snapshot arrives.
- **Analyzers:** findings appear as status chips under the description (yellow running, gray idle, red has findings).
  - Findings display inline, **styled differently from human comments**, with previewable fixes.
  - **"Please fix" converts an analyzer finding into a real unresolved comment.**
  - Actionability is binary: highlighted or not.
- **Readiness:** a scoring panel shows who LGTM'd, which approvals are missing and why, and the unresolved count. **The page header turns green when the change is ready.**
- **ML-suggested edits:** authors resolve ~7.5% of reviewer comments by applying them ([Google Research](https://research.google/pubs/resolving-code-review-comments-with-machine-learning/)).

### Meta ([engineering blog](https://engineering.fb.com/2022/11/16/culture/meta-code-review-time-improving/))
- **Next Reviewable Diff** works like autoplay: after finishing a review, ML surfaces the next diff you're likely to review.
  - +17% review actions per day; users of the flow do 44% more review actions.
- **Nudgebot** pings likely reviewers on stale diffs with quick actions and "Remind Me Later" (−7% time in review).
- Metrics: P75 **Time In Review** as the north star, with **Eyeball Time** as a guardrail against rubber-stamping.

---

## 5. AI-native review tools

| Tool | How findings are shown | Chat ↔ code | Acting on findings |
|---|---|---|---|
| **Devin Review** | **Analysis sidebar**: overview + key-change bullets. Bugs are Severe (red) or Non-severe (orange); Flags are Investigate (orange) or Informational (gray); Security is Critical or Warning, with CWE. Each finding has "Learn more" and a copy button. **Resolved items dim and sort to the bottom** ([docs](https://docs.devin.ai/work-with-devin/devin-review)) | "Ask Devin" **from any comment, bug, or flag**; codebase-aware. Links to comments scroll to and highlight the target ([docs](https://docs.devin.ai/work-with-devin/devin-review)) | Chat proposes edits you apply as a commit. Auto-fix on Devin PRs. Merge bar with merge/close/draft/auto-merge ([docs](https://docs.devin.ai/work-with-devin/devin-review)) |
| **CodeRabbit Review ("Change Stack")** | **3 panels**: cohort/layer rail (left), layer-scoped diff (center), **per-range AI summary that tracks scroll** (right). "Needs your attention" list and merge-readiness ([blog](https://coderabbit.ai/blog/introducing-atlas-the-first-ai-native-code-review-interface), [docs](https://docs.coderabbit.ai/pr-reviews/coderabbit-review)) | Chat opens **from a layer, file, or range**. A chat thread keeps answering about the snapshot it was opened on ([docs](https://docs.coderabbit.ai/pr-reviews/coderabbit-review)) | `J`/`K` layers, `Z` focus mode. Mark viewed, draft review, apply suggestions, merge or enqueue. A **snapshot dropdown** answers "what changed since I last looked?" ([blog](https://coderabbit.ai/blog/introducing-atlas-the-first-ai-native-code-review-interface)). **Semantic diff** shows moves as moves and token-level edits, with mechanical noise grouped ([blog](https://coderabbit.ai/blog/introducing-semantic-diff)) |
| **Greptile** | Top-level summary with a **0–5 confidence score** mapped to actions (5 = merge … 0–1 = rethink). Per-file breakdown and auto-chosen diagram type. Inline **P0/P1/P2 badges** plus Logic/Syntax/Style types ([docs](https://greptile.com/docs/code-review/first-pr-review)) | `@greptileai` replies | Most comments carry a suggested fix diff. Footer shows the last reviewed commit and a re-run button ([docs](https://greptile.com/docs/code-review/first-pr-review)) |
| **Cursor Bugbot** | Inline comments with an explanation and fix. Summary in the PR description (default) or a comment, with an optional risk score. A neutral CI check unless set to fail on unresolved ([docs](https://cursor.com/docs/bugbot)) | n/a | **"Fix in Cursor" / "Fix in Web"** on each finding. Autofix with a Cloud Agent, either to a new branch or committed to the PR branch (max 3 attempts) ([docs](https://cursor.com/docs/bugbot)). Users want a PR-level "Fix all" ([forum](https://forum.cursor.com/t/fix-all-in-cursor-button-for-bugbot-pr-reviews/146734)) |
| **Copilot code review** | H/M/L severity badge; duplicates grouped; auto-resolve on fix (Section 3) | Chat panel beside the diff at PR, file, or lines scope | Fix with Copilot dialog; Fix batch |
| **Linear Diffs** (May 2026) | Agent comments show the agent's name, avatar, and model. A 4-level **risk score** via HTML-comment metadata ([docs](https://linear.app/docs/diffs)) | **Agent changes update the diff in real time** with no local checkout ([changelog](https://linear.app/changelog/2026-05-27-linear-diffs)) | **Guided review** tab: core change first, glue code separate, each section explains *why* ([docs](https://linear.app/docs/diffs)) |
| **Codex code review** | One view of PRs, changes, findings, comments, and checks. Suggested order: description → files → findings and comments → checks → questions ([OpenAI help](https://help.openai.com/en/articles/20001552-review-pull-requests-with-codex)) | Ask Codex to explain a change | Ask Codex to prepare a fix for a finding |
| **Qodo 2.0** | **Multi-agent findings consolidated into one prioritized review.** A "Finding Recommendation Agent" **deprioritizes finding types your team historically ignores** ([FAQ](https://docs.qodo.ai/install-and-configure/migrating-to-qodo-v2/migrating-to-qodo-v2-faq)) | | |
| **Ellipsis** | Each comment has a confidence score with a per-customer threshold, then dedup and hallucination filters ([blog](https://www.ellipsis.dev/blog/how-we-built-ellipsis)). Reports % of comments addressed by severity ([product](https://docs.ellipsis.dev/features/code-review)) | Reply to train | |
| **Vercel Agent** | **Only posts suggestions validated in a sandbox** against real builds, tests, and linters; one-click apply ([docs](https://vercel.com/docs/agent/pr-review)) | | |
| **Tusk** | Non-blocking PR check that proposes tests and shows pass/fail ([summary](https://aicoolies.com/reviews/tusk-review)) | | One-click commit of tests to the branch |

### More on Linear Diffs ([docs](https://linear.app/docs/diffs), [changelog](https://linear.app/changelog/2026-05-27-linear-diffs))
- **Reviews sidebar:** "For me" / "Created", **default order by "closest to shipping"**. Optional fields for failed checks and preview links.
- **Line counts exclude tests and docs by default**, with `[*]` showing the full total on hover. Files can be grouped by `.gitattributes` category (tests, docs, generated).
- **Structural (syntax-aware) highlighting** marks the changed tokens in a line.
- **Stack position** is shown with each PR's review, check, and merge status.
- `Cmd+B` toggles split/unified. Commit-by-commit filtering. Merging a GitHub-native stack shows how many PRs are affected.
- **Why Linear feels instant:**
  - a local IndexedDB replica with an in-memory object pool
  - optimistic mutations with a queued transaction log
  - comments and history deferred from bootstrap
  - delta sync after the first load ([performance.dev](https://performance.dev/how-is-linear-so-fast-a-technical-breakdown), [Linear blog](https://linear.app/blog/scaling-the-linear-sync-engine))

### Patterns across AI tools
1. **Findings live in a dedicated, sortable list *and* inline.** Devin, CodeRabbit, and Critique all keep a findings panel separate from the human comment stream, and inline findings are styled differently from human comments ([Critique](https://abseil.io/resources/swe-book/html/ch19.html), [Devin](https://docs.devin.ai/work-with-devin/devin-review)).
2. **2–3 severity levels, never more.** Copilot uses H/M/L, Devin uses severe/non-severe plus flags, and Greptile uses P0–P2. Critique deliberately went binary.
3. **Every finding is a launch point:** "Ask" (chat seeded with the finding), "Fix" (agent), "Please fix" (promote to a human comment), "Dismiss/Resolve" (dim and sink).
4. **Batching:** "Fix batch" (Copilot) and "Fix all" (requested for Bugbot).
5. **Verdict at a glance:** Greptile 0–5, a Linear/Bugbot risk score, a CodeRabbit merge-readiness line, and Critique's green header.
6. **Reading order beats alphabetical order:** Devin smart grouping, CodeRabbit cohorts/layers, Linear guided review, Stage chapters. HN pushback says the grouping must show *why* and must not hide un-highlighted code ([HN](https://hn.svelte.dev/item/47796818)).

---

## 6. Performance techniques for diff/review UIs

| Technique | Evidence | Notes for us |
|---|---|---|
| **Structure before content** (file list + stats first, patches progressively) | GitHub 2016: 400-line / 20 KB first batch, ~3× p-high ([post](https://github.blog/engineering/architecture-optimization/how-we-made-diff-pages-3x-faster/)); Copilot app ([post](https://github.blog/engineering/user-experience/rendering-huge-pull-requests-in-the-github-copilot-app)); GitLab first batch inline ([docs](https://docs.gitlab.com/development/fe_guide/rapid_diffs/)) | Split our PR fetch into shell / files list / patches |
| **Virtualization with known heights** | GitHub TanStack Virtual for p95+ ([post](https://github.blog/engineering/architecture-optimization/the-uphill-climb-of-making-diff-lines-performant/)); Pierre height estimates + inverse sticky ([post](https://pierre.computer/writing/on-rendering-diffs)) | Use `CodeView` for one virtualized scroll of all files |
| **Off-main-thread highlighting, plain text first** | Pierre worker pool + LRU + `prime` ([post](https://pierre.computer/writing/on-rendering-diffs)); Copilot app ([post](https://github.blog/engineering/user-experience/rendering-huge-pull-requests-in-the-github-copilot-app)); Shiki recommends workers and its JS engine for smaller bundles and faster startup ([Shiki best performance](https://shiki.style/guide/best-performance), [regex engines](https://shiki.style/guide/regex-engines)) | We already have `WorkerPoolContextProvider`; add `prime` on hover/scroll proximity |
| **Few components per line, delegated events, no per-line effects** | GitHub v2: 74% fewer components, INP −78% ([post](https://github.blog/engineering/architecture-optimization/the-uphill-climb-of-making-diff-lines-performant/)) | Annotations (comments, findings) render only on lines that have them |
| **Comment blocks anchored to (file, line, side) with fingerprinted cached heights** | Copilot app ([post](https://github.blog/engineering/user-experience/rendering-huge-pull-requests-in-the-github-copilot-app)) | Matters once threads and findings are inline |
| **`content-visibility: auto` + `contain-intrinsic-size: auto <est>`** | 232 → 30 ms render in web.dev demo ([web.dev](https://web.dev/articles/content-visibility)); GitLab uses it per file ([docs](https://docs.gitlab.com/development/fe_guide/rapid_diffs/)) | Cheap win for the timeline/conversation column |
| **Intent prefetch** | instant.page: at 65 ms of hover, ~50% click probability, leaving >300 ms head start ([instant.page](https://instant.page/)); TanStack `preload="intent"` with a 50 ms default delay, touchstart immediate ([docs](https://tanstack.com/router/latest/docs/framework/react/guide/preloading)); Speculation Rules `moderate` = 200 ms hover or pointerdown, Chrome cap 2 ([Chrome](https://developer.chrome.com/blog/speculation-rules-improvements)) | Already `defaultPreload: "intent"`; make the loader prefetch the *shell* queries only, not patches |
| **Retained cache of recent PRs** | Copilot app keeps the last few diff documents resident and evicts older ones ([post](https://github.blog/engineering/user-experience/rendering-huge-pull-requests-in-the-github-copilot-app)); GitHub-client uses a SQLite cache ([PR](https://github.com/A-and-Brian/GitHub-client/pull/1)); Linear's IndexedDB replica | TanStack Query `gcTime` for the last ~5 PRs; consider query persistence for the inbox and PR shells |
| **SSR layout state via cookies** | GitLab persists tree width/visibility in cookies to avoid layout shift ([docs](https://docs.gitlab.com/development/fe_guide/rapid_diffs/)) | We have TanStack Start SSR: put panel widths and the chat-open state in a cookie |
| **Prefetch adjacent states** | Critique prefetches snapshots ([book](https://abseil.io/resources/swe-book/html/ch19.html)); Meta's Next Reviewable Diff ([blog](https://engineering.fb.com/2022/11/16/culture/meta-code-review-time-improving/)) | Prefetch the next PR in the inbox queue and the next version's file list |
| **Memory hygiene** | Detach substrings from the patch, pool DOM containers, share options state ([Pierre](https://pierre.computer/writing/on-rendering-diffs)) | Mostly inside the library |
| **Keep find-in-page** | GitHub's virtualized mode loses Ctrl+F; the code view used a hidden textarea ([2023 post](https://github.blog/engineering/architecture-optimization/crafting-a-better-faster-code-view/), [Jan 2026](https://github.blog/changelog/2026-01-22-improved-pull-request-files-changed-page-on-by-default/)) | Provide our own `Cmd+F` diff search when virtualized |
| **Measure INP by diff size; perf budgets in E2E** | GitHub Datadog segmentation; Copilot app's probe lane ([GitHub](https://github.blog/engineering/architecture-optimization/the-uphill-climb-of-making-diff-lines-performant/), [Copilot app](https://github.blog/engineering/user-experience/rendering-huge-pull-requests-in-the-github-copilot-app)) | Add a RUM INP tag for `diff_lines` buckets |
| **Streaming highlighter for chat code blocks** | `@shikijs/stream` tokenizes chunks as they arrive without re-tokenizing ([Shiki](https://shiki.style/packages/stream)) | For agent chat responses with code |

---

## What we should steal (ranked, opinionated)

### 1. Layout: one page, three columns, zero tabs to the diff
- **Left (collapsible, `F`):** file tree / review map. It shows the viewed state, per-file markers (finding severity dot, unresolved-thread dot, check-annotation dot), and generated files collapsed (`linguist-generated`). This is GitHub's markers ([changelog](https://github.blog/changelog/2025-06-26-improved-pull-request-files-changed-experience-now-in-public-preview/)) plus Graphite's `.gitattributes` handling ([docs](https://graphite.com/docs/pr-page-overview)).
- **Center (one continuous scroll):** header → description (collapsed after 6 lines) → **findings summary** → **all file diffs in a single `CodeView`**. Do *not* put the diff behind a tab. That extra click is exactly what made users abandon Cursor Review ([forum](https://forum.cursor.com/t/the-updated-pr-ui-is-a-huge-step-back/166402)). The timeline moves to the right rail.
- **Right rail (resizable, cookie-persisted):** tabs **Chat · Activity · Checks**. Default to **Chat**. Clicking the active tab collapses the rail into **Focus mode**, as Graphite does ([docs](https://graphite.com/docs/review-pull-requests)). Persist width and open state in a cookie so SSR renders the final layout, as GitLab does ([docs](https://docs.gitlab.com/development/fe_guide/rapid_diffs/)).
- **Sticky header strip:**
  - title, author → base, stack navigator (`S`) — stack prominence is what users valued ([forum](https://forum.cursor.com/t/the-updated-pr-ui-is-a-huge-step-back/166402), [GitHub stacks](https://github.github.com/gh-stack/guides/ui/))
  - a **"whose turn" chip** (Gerrit attention set: bold name + waiting duration) ([Gerrit](https://gerrit-review.googlesource.com/Documentation/user-attention-set.html))
  - a **merge-readiness pill** that turns green when mergeable (Critique) ([book](https://abseil.io/resources/swe-book/html/ch19.html))
  - a review progress bar (viewed / total, as on GitHub)

### 2. Chat: the right rail, always one keystroke away, wired to the diff
- **`Cmd+;` focuses chat**, matching Graphite ([blog](https://graphite.com/blog/introducing-graphite-chat)). Chat is open by default on wide screens (≥1440 px) and becomes a slide-over below that.
- **Context chips:** selecting lines (or `Shift+A` on a selection) adds an "Add to chat" chip carrying `path:L10-24` and the code. This copies Graphite's line menu ([docs](https://graphite.com/docs/pr-page-overview)) and Copilot's file/lines scopes ([docs](https://docs.github.com/en/enterprise-cloud@latest/copilot/tutorials/explore-pull-requests)).
  - Every finding, thread, and failed check gets an **"Ask"** action that seeds the chat with that object, as Devin does ([docs](https://docs.devin.ai/work-with-devin/devin-review)).
- **Chat → code:** every file or line reference in an answer is a link that scrolls `CodeView` to the line and flashes it. Use the same "scroll and highlight" behavior Devin applies to comment links ([docs](https://docs.devin.ai/work-with-devin/devin-review)) and Graphite applies to timeline comments ([docs](https://graphite.com/docs/review-pull-requests)). Hovering a reference shows a 5-line peek.
- **Agent edits stream into the diff live.** When the agent pushes, the diff updates in place without a reload, as in Linear ([changelog](https://linear.app/changelog/2026-05-27-linear-diffs)) and GitHub's no-reload refresh ([changelog](https://github.blog/changelog/2026-01-22-improved-pull-request-files-changed-page-on-by-default/)). Show a "N files changed by agent since you looked" banner with a "show only agent changes" filter.
- **Pin chat to the head SHA it was asked about.** If the PR moved, show a stale marker, as CodeRabbit does ([docs](https://docs.coderabbit.ai/pr-reviews/coderabbit-review)).

### 3. AI findings: a ranked queue, styled unlike human comments, every one actionable
- **Findings card** at the top of the center column: a one-line verdict (e.g., "2 must-fix · 3 consider · checks failing"). Use a short label, not a long summary; Pyor deliberately avoids long summaries ([dev.to](https://dev.to/pyor/github-pr-review-alternatives-in-2026-an-honest-comparison-4m0h)).
  - **Max 3 severities** (Must fix / Consider / FYI), following Copilot ([changelog](https://github.blog/changelog/2026-05-12-copilot-code-review-comment-experience-improvements)), Devin ([docs](https://docs.devin.ai/work-with-devin/devin-review)), and Critique's restraint ([book](https://abseil.io/resources/swe-book/html/ch19.html)).
  - **Group duplicates** across files, as Copilot does.
- **Inline in the diff,** findings render as annotations with a distinct style (AI glyph, tinted gutter), never as GitHub-style comment bubbles ([Critique](https://abseil.io/resources/swe-book/html/ch19.html)). Human threads stay visually primary.
- **Actions on each finding:**
  - **Ask** (seed chat)
  - **Fix** (agent; dialog: commit to this PR vs new PR, plus an optional instruction) ([Copilot](https://github.blog/changelog/2026-05-19-easily-apply-copilot-code-review-feedback-with-copilot-cloud-agent))
  - **Post as comment** (Critique's "Please fix" promotes a finding to a real unresolved review comment)
  - **Dismiss** (dims and sinks to the bottom, like Devin; 👎 asks why, like Graphite ([docs](https://graphite.com/docs/ai-review-experimental-comments)))
- **Batch fix:** a checkbox per finding plus "Fix selected (N)" ([Copilot](https://github.blog/changelog/2026-05-19-easily-apply-copilot-code-review-feedback-with-copilot-cloud-agent), [Bugbot request](https://forum.cursor.com/t/fix-all-in-cursor-button-for-bugbot-pr-reviews/146734)).
- **Auto-resolve findings** when a later commit addresses them, and say so ("resolved by a1b2c3d") ([Copilot](https://github.blog/changelog/2026-09-11-auto-resolution-and-analysis-updates-in-copilot-code-review)).

### 4. Diff viewer behavior
- **Switch to `@pierre/diffs` `CodeView`** for one virtualized scroll across all files. It is already in our installed 1.3.6 but unused. We get sticky file headers, `scrollTo`, inverse-sticky zero-blanking, and model-based scroll anchoring for free ([Pierre](https://pierre.computer/writing/on-rendering-diffs)).
- **Viewed state:**
  - `v` toggles it; viewed files collapse, and `v` also advances to the next file
  - fast-reviewer's `r` does "mark viewed + next" ([repo](https://github.com/matmeylan/fast-reviewer)); GitLab binds `v` to viewed ([docs](https://docs.gitlab.com/user/shortcuts/))
  - **auto-unmark when the file changes**, as GitHub does ([docs](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/reviewing-changes-in-pull-requests/reviewing-proposed-changes-in-a-pull-request))
  - store per user per head SHA for a **"Changes since your last review"** toggle ([Gitea](https://gitea.com/dco/gitea/src/branch/main/tests/integration/pull_diff_test.go), [Reviewable](https://docs.reviewable.io/files), [CodeRabbit snapshots](https://coderabbit.ai/blog/introducing-atlas-the-first-ai-native-code-review-interface))
- **Two orderings, user-toggled:**
  - **Tree** (default, deterministic)
  - **Guided** (AI-ordered groups with one-line *why* per group: Devin / CodeRabbit layers / Linear guide). Never hide files outside a group, because of the HN criticism about rubber-stamping ([HN](https://hn.svelte.dev/item/47796818)).
  - Remember the choice per user.
- **Collapsed by default:** generated files, lockfiles, and snapshots. Show line counts excluding tests and docs, with the full total on hover ([Linear](https://linear.app/docs/diffs)).
- **Comments:** comment on any line; drag for ranges; **drafts autosave locally** ([GitHub](https://github.blog/changelog/2026-01-22-improved-pull-request-files-changed-page-on-by-default/)). `i` minimizes all threads ([GitHub](https://github.blog/changelog/2025-06-26-improved-pull-request-files-changed-experience-now-in-public-preview/)).
- **Later:** move detection and token-level intraline highlighting ([Critique](https://abseil.io/resources/swe-book/html/ch19.html), [CodeRabbit semantic diff](https://coderabbit.ai/blog/introducing-semantic-diff)).
- **Keep `Cmd+F` working:** if virtualization breaks native find, intercept `Cmd+F` inside the diff and search the model ([GitHub 2023](https://github.blog/engineering/architecture-optimization/crafting-a-better-faster-code-view/)).

### 5. Timeline (right-rail "Activity" tab)
- Show a compact event list. Collapse bot noise (CI pushes, bot comments) into "N automated events". Reviews show as one row with a count of inline comments.
- **Clicking any line-anchored item scrolls the diff to it** ([Graphite](https://graphite.com/docs/review-pull-requests)).
- Group unresolved threads at the top as **To reply / Unresolved / Resolved** ([Reviewable](https://docs.reviewable.io/discussions)).
- `n`/`p` cycles open threads ([GitLab](https://docs.gitlab.com/user/shortcuts/)).
- Use `content-visibility: auto` on items ([web.dev](https://web.dev/articles/content-visibility)).

### 6. Merge box
- Keep a compact status in the sticky header, plus the full box in the right rail's **Checks** tab and at the end of the center column.
- Put **merge checks first**, before the description, as GitLab's redesign does ([issue](https://gitlab.com/gitlab-org/gitlab/-/work_items/586835)).
- Group checks by status with **failing first** and natural sort ([GitHub](https://github.blog/changelog/2025-03-04-improved-pull-request-merge-experience-is-now-generally-available/)).
- Each failing check gets **"Fix with agent"** and **"Ask why"** ([Copilot app](https://docs.github.com/en/copilot/how-tos/github-copilot-app/managing-issues-and-pull-requests), [Graphite](https://graphite.com/blog/introducing-graphite-chat)).
- Add **Action Cards** for the single next step: "Resolve 2 threads", "Re-request review", "Update branch", "Merge" ([Graphite](https://graphite.com/features/pr-page)).
- Offer an **"Agent merge" toggle** that watches CI and reviews and merges when allowed ([Copilot app](https://docs.github.com/en/copilot/how-tos/github-copilot-app/managing-issues-and-pull-requests)).
- Report ruleset or commit-message failures at merge time ([GitHub](https://github.blog/changelog/2025-03-04-improved-pull-request-merge-experience-is-now-generally-available/)).

### 7. Keyboard shortcuts (show them on `?`)
- Use GitLab-compatible navigation, which is the most complete public set ([docs](https://docs.gitlab.com/user/shortcuts/)), plus Graphite's review chords ([docs](https://graphite.com/docs/pr-page-overview)).

| Key | Action |
|---|---|
| `j` / `k` | next / previous file |
| `n` / `p` | next / previous unresolved thread **or finding** (one attention queue) |
| `v` | toggle viewed and advance |
| `F` | toggle file tree |
| `S` | stack navigator |
| `Cmd+P` | file finder |
| `Cmd+K` | command palette |
| `Cmd+;` | focus chat |
| `Shift+A` | add selection to chat |
| `c` | comment on the selection |
| `Cmd+Enter` | add to review |
| `Shift+Cmd+Enter` | post now |
| `R A` / `R C` / `R N` | approve / comment / request changes |
| `Shift+D` | split ↔ unified |
| `i` | minimize comments |
| `Z` | focus mode ([CodeRabbit](https://coderabbit.ai/blog/introducing-atlas-the-first-ai-native-code-review-interface)) |
| `?` | help |

- After approving, **offer the next PR in your queue** (Meta's Next Reviewable Diff: +17% review actions) ([blog](https://engineering.fb.com/2022/11/16/culture/meta-code-review-time-improving/)).

### 8. Perf and prefetch strategy (concrete)
1. **Split the PR into independent queries** so each region renders as soon as its data lands:
   - `prShell` (title, state, author, head/base SHAs, mergeability, reviewers, check summary)
   - `prFiles` (paths + stats only)
   - `prPatches` (batched)
   - `prTimeline`
   - `prChecks`
   - `prFindings`
   - `chatHistory`

   This mirrors GitHub's structure-before-content ([2016](https://github.blog/engineering/architecture-optimization/how-we-made-diff-pages-3x-faster/), [Copilot app](https://github.blog/engineering/user-experience/rendering-huge-pull-requests-in-the-github-copilot-app)).
2. **Route loader on hover intent** (already `defaultPreload: "intent"`, 50 ms) prefetches only `prShell` + `prFiles` + `prFindings` summary. These are small and make the first paint complete. Do not prefetch patches on hover. instant.page's data says a 65 ms hover is a coin-flip click with ~300 ms of head start ([instant.page](https://instant.page/)).
3. **On page mount:**
   - fetch the first patch batch (~400 lines / 20 KB, per GitHub's data) server-side during SSR
   - stream the rest in the background
   - `prime` the Pierre worker pool for the next N files by scroll proximity ([Pierre](https://pierre.computer/writing/on-rendering-diffs))
   - render plain text first, highlight later
4. **Exact-size skeletons only**, derived from `prFiles` stats (`lineHeight × lines`), so nothing shifts when patches arrive ([Pierre](https://pierre.computer/writing/on-rendering-diffs), [web.dev](https://web.dev/articles/content-visibility)).
5. **Retain the last ~5 PRs** in the Query cache, so back/forward and inbox→PR→inbox repaint instantly and then revalidate ([Copilot app](https://github.blog/engineering/user-experience/rendering-huge-pull-requests-in-the-github-copilot-app)).
   - Also prefetch the **next PR in the queue** after approve ([Meta](https://engineering.fb.com/2022/11/16/culture/meta-code-review-time-improving/)) and the **next stack PR's shell** when the stack navigator is visible.
6. **Live updates over SSE** for the timeline, checks, and agent pushes. Apply optimistic updates for comment, resolve, viewed, approve, and merge (Linear's model ([performance.dev](https://performance.dev/how-is-linear-so-fast-a-technical-breakdown)); our AGENTS.md already mandates optimistic UI).
7. **Render rules for line-level code:**
   - no per-line `useEffect`, enforced by a lint rule
   - delegated events via `data-*`
   - annotations rendered only where present
   - `Map` lookups by `path:line:side` ([GitHub](https://github.blog/engineering/architecture-optimization/the-uphill-climb-of-making-diff-lines-performant/))
8. **Measure:** RUM INP and LCP tagged by `diff_lines` bucket and PR file count. A Playwright perf budget on a synthetic 5k-file PR fixture asserts mounted rows, no late insertions, and INP under 100 ms ([GitHub](https://github.blog/engineering/architecture-optimization/the-uphill-climb-of-making-diff-lines-performant/), [Copilot app](https://github.blog/engineering/user-experience/rendering-huge-pull-requests-in-the-github-copilot-app)).
