# SplitView

Panes have roles, not sides. One is the work pane, where the decision happens; the other is either the reference pane (facts the decision is made against) or the list pane (the triage sidebar that picks which decision is on screen). Nami...

Import: `@langchain/gtm-platform-design-system/patterns/split-view`
Source: `src/patterns/split-view.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### SplitViewBase

| Prop | Type | Required |
|---|---|---|
| `workPane` | `ReactNode` | yes |
| `workLabel` | `string` | yes |
| `posture` | `SplitViewPosture` | no |
| `workScroll` | `"pane" \| "contained"` | no |

### SplitViewReference

| Prop | Type | Required |
|---|---|---|
| `referencePane` | `ReactNode` | yes |
| `referenceLabel` | `string` | yes |
| `referenceHidden` | `boolean` | no |
| `referenceSeam` | `boolean` | no |
| `referenceWidth` | `SplitViewReferenceWidth` | no |
| `referenceScroll` | `"pane" \| "contained"` | no |
| `listPane` | `never` | no |
| `listLabel` | `never` | no |
| `listCollapsed` | `never` | no |
| `onListCollapsedChange` | `never` | no |
| `referenceResizable` | `boolean` | no |
| `listResizable` | `never` | no |
| `listWidth` | `never` | no |
| `onListWidthChange` | `never` | no |

### SplitViewList

| Prop | Type | Required |
|---|---|---|
| `listPane` | `ReactNode` | yes |
| `listLabel` | `string` | yes |
| `referencePane` | `never` | no |
| `referenceLabel` | `never` | no |
| `referenceHidden` | `never` | no |
| `referenceSeam` | `never` | no |
| `referenceWidth` | `never` | no |
| `referenceScroll` | `never` | no |
| `referenceResizable` | `never` | no |
| `listCollapsed` | `boolean` | no |
| `onListCollapsedChange` | `(collapsed: boolean) => void` | no |
| `listResizable` | `boolean` | no |
| `listWidth` | `number` | no |
| `onListWidthChange` | `(width: number) => void` | no |

## The decisions this carries

- Panes have roles, not sides. One is the work pane, where the decision happens; the other is either the reference pane (facts the decision is made against) or the list pane (the triage sidebar that picks which decision is on screen). Naming them is the whole pattern: 'left' and 'right' cannot tell you which one is allowed to shrink.
- Two compositions, never a mash-up: `work + reference` (decision grows, context yields) or `list + work` (inner sidebar, decision grows). Inbox is list + work. Do not put the conversation list in `workPane` and the thread in a narrow `referencePane` — that inverts roles and cramps ApprovalArtifact.
- List chrome belongs to the list pane. Title, search, status Tabs, and grouping mount inside `listPane` via `ListSidebarChrome` — never as a full-width strip above the SplitView. A filter that spans the thread is claiming territory it does not own. Title-row Filters and Group by are icon-only (`trigger="icon"`, `icon-sm` ghost). The noun lives on `aria-label`. A labeled outline Group by belongs on the FilterableTable toolbar, not beside a list title.
- List sidebar titles use `ListSidebarTitle` — optional leading glyph at `Icon` `lg` (20px) plus `text-title font-semibold`, on the `h-control` rung. That rung is not decoration: `AppRailBrand` is `h-control`, and the list title sits across one vertical seam from it, so they are the same construction rather than two numbers that happen to add up.
- List chrome top air is `pt-2`, which is the rail's own `pt-2` — the same air above the product wordmark, so the two mastheads share a baseline. Never re-derive it by adding a child's padding to the rail's; that arithmetic drifts the moment either row changes rung, and it did. Bottom air is `pb-2` — the same pad the alert inspector header spends above its hairline. Never override it at a call site. The stack gap is `sm` for title, search, and tabs; do not invent a second gap between search and tabs. Host-scroll shell cards stay flush so the list pane and resize handle go edge-to-edge — never park AppShell card `pt-2` above a SplitView.
- One lane down the whole pane: `ListSidebarChrome` owns `px-3`, and rows own `px-3` themselves and sit flush in the scroll surface. Never wrap the rows in a padding container — a second, smaller inset puts the rows out of line with the title and search above them, and puts one list pane out of line with every other. States inside the scroll surface (error, empty) take `Box padding="md"`, not the row lane.
- The list pane owns its scroll. Sticky chrome sits above a ScrollArea of rows; SplitView does not wrap `listPane` in a second ScrollArea (that would scroll the chrome away). The work pane defaults to the same pattern's ScrollArea. A table that must keep its toolbar put takes `workScroll="contained"`: the pane fills the host and does not scroll, and FilterableTable's grid body is the scroll surface.
- The pane that holds reference yields; the pane where the decision happens keeps its width. When the agent docks at 420px the reference pane narrows (or, in list + work without a user width, the list narrows a step) and the work pane does not move. A surface where the conversation shrinks so a fact panel can stay wide has its roles backwards.
- A closed reference pane hides with CSS (`referenceHidden`): width 0, no seam, still mounted. Accounts closes the record panel and the table stays; Alerts does the same for the inspector. Do not swap the split for a single pane, and do not park an empty state in a closed rail.
- Inspection opens from an explicit row click and preserves the list or table's filters, scroll, selection, and draft state. Start with the reference pane closed when no standing reference is required; do not reserve an empty inspector to advertise that rows are clickable. A full route is for a durable workspace, not a read-only peek that makes the user rebuild list context on Back.
- A reference pane that holds a framed artifact (the Alerts inspector) takes `referenceSeam={false}`. The Frame is the outline; a pane hairline beside it is a second border. That inspector also takes `referenceScroll="contained"` so identity and hops stay put while the body scrolls — the pattern's ScrollArea would otherwise roll the chrome away.
- Per-surface yield, settled: overview, the canvas re-centers and nothing is lost; inbox, the list sidebar steps down and the thread stays; accounts, the record panel closes and the table stays; campaign, the outcomes rail folds into the snapshot; data grids scroll horizontally with the first column sticky.
- Thread, draft, approval state, scroll position and composer text survive every posture change. A posture is a width, not a route: if toggling the dock loses a half-written reply, the bug is that something was unmounted, not that the user should have saved.
- That contract is why posture never swaps one component for another. Both panes stay mounted in the same tree positions in every posture, neither takes a key that varies, and a pane that must disappear hides with CSS rather than being conditionally rendered. Rendering `posture === 'docked' ? <Chips/> : <Rail/>` remounts the subtree and throws away exactly the state this rule protects. List collapse is the same law at a narrower width (48px icon rail) — the pane stays mounted.
- Anything a pane must not lose across a posture change lives in that pane's own component or above the SplitView, never in a wrapper the posture recreates.
- Two panes, never three. A third region on a surface is the agent dock, and the dock is the shell's, not this pattern's; a split view that grew a third pane is a surface that needed a route. `listPane` and `referencePane` are mutually exclusive.
- Panes divide the measure; rails sit outside it. AppShell's `contentWidth` caps the content region, and a SplitView mounted there splits what the cap leaves. The navigation rail and the agent dock are the shell's chrome, outside the measure. (`APP_SHELL_RULES`; `docs/plan/gtm-agent-product/width-padding-evidence.md` section 5.3 rules 2, 4 and 7.)
- AppShell that mounts this pattern MUST set `contentScroll="host"` (focus already defaults). Shell ScrollArea is the root bug: it collapses `h-full`, so list + work pile at the top of an empty viewport. ESLint `gtm/design-law/app-shell-host-scroll` enforces it. Never paper over a missing host with page-local `h-screen` / sticky hacks.
- List sidebar defaults: 384 open / 320 docked / 48 collapsed. Inbox opts into ReUI app-shell-4's **440** open list (`listWidth={440}` as the initial width when uncontrolled; drag clamped **200–520**) plus `listCollapsed`. Reference context stays 300 / docked 200 (CORE 02 NKJ-0, CORE 07 QUO-0). A report artifact (Alerts, Scheduled) takes `referenceWidth="report"`: 400 / docked 320 (CORE 08 RMO-0). `referenceResizable` puts a seam handle on the leading edge (clamped **280–640**); drag disables the width ease so the handle tracks 1:1.
- Opening a reference pane must not change AppShell `contentWidth`. The work column stays; the rail eases in. Switching reading→wide recenters the list the user was looking at.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
