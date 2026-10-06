# AgentThreadRail

In focus mode this rail replaces the navigation rail — it is not a third sidebar. Width follows AppShell's rail (246–400px, default 246).

Import: `@langchain/gtm-platform-design-system/patterns/agent-thread-rail`
Source: `src/patterns/agent-thread-rail.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### AgentThreadRail

| Prop | Type | Required |
|---|---|---|
| `onDropThread` | `(id: string, target: ThreadRailDropTarget) => void` | no |
| `groups` | `readonly ThreadRailGroup[]` | yes |
| `features` | `readonly ThreadRailFeature[]` | no |
| `leading` | `ReactNode` | no |
| `compact` | `boolean` | no |
| `search` | `string` | no |
| `onSearchChange` | `(value: string) => void` | no |
| `onSelectThread` | `(id: string) => void` | no |
| `onPrefetchThread` | `(id: string) => void` | no |
| `groupBy` | `ThreadRailGroupBy` | no |
| `onGroupByChange` | `(next: ThreadRailGroupBy) => void` | no |
| `sort` | `ThreadRailSort` | no |
| `onSortChange` | `(next: ThreadRailSort) => void` | no |
| `filter` | `ThreadRailFilter` | no |
| `onFilterChange` | `(next: ThreadRailFilter) => void` | no |
| `collapsedGroupIds` | `readonly string[]` | no |
| `onCollapsedGroupIdsChange` | `(ids: readonly string[]) => void` | no |
| `projects` | `readonly ThreadRailProject[]` | no |
| `onPinThread` | `(id: string, pinned: boolean) => void` | no |
| `onMarkThreadRead` | `(id: string) => void` | no |
| `onMarkThreadUnread` | `(id: string) => void` | no |
| `onArchiveThread` | `(id: string) => void` | no |
| `onRenameThread` | `(id: string) => void` | no |
| `onDeleteThread` | `(id: string) => void` | no |

## The decisions this carries

- In focus mode this rail replaces the navigation rail — it is not a third sidebar. Width follows AppShell's rail (246–400px, default 246).
- Agent features are real `SidebarNav` / `SidebarNavItem` rows (New thread, Scheduled, Skills with Zap, …) — same page rungs as the product rail. Mount via `features` with `SidebarNav flush`. No standalone New thread button; creation lives in the feature list or the composer.
- List chrome follows the narrow-rail law: ONE filter menu for grouping / ordering / show. It lives on the Conversations group header, not on a second CONVERSATIONS masthead above the groups. Standing `SearchInput` is enabled by the host through `onSearchChange`; never park Search + Filter + Group as three peers in a 246px row.
- The host defaults to projects when projects exist and restores the reader's chosen grouping. Origin grouping remains available; status is shown on each thread, never as a group. Running threads show a spinner. Otherwise, unread threads show a blue dot; read threads have no blue dot. Blocked or failed read threads retain an attention mark. Pinned conversations and pinned project disclosures share the first PINNED section, independently of grouping. Slack-origin rows use the same title-and-time line as web rows, plus a leading 16px Slack mark in a muted compact well. Never put a 24px ProviderMark well on the row: that grows the control rung.
- Read and unread titles use the same weight. A blue dot means unread, never completed. Opening a thread clears its dot; Mark as read or Mark as unread in the row menu changes it through the same principal-scoped label mutation. Explicitly marking the open thread unread keeps its dot until marked read or reopened.
- Drag a thread onto a project or No project to move it and clear its individual pin. Drop onto Pinned to pin it while retaining its project. Drops use one host-owned label update; row menus remain the keyboard alternative.
- Groups are collapsible. Non-project groups put the disclosure chevron to the right of the label. A project group uses its chosen identity glyph, or Folder closed and FolderOpen open when none was chosen, and never a chevron beside it. A section header is a category disclosure, not a destination.
- Project headings carry the project identity; other headings use a disclosure chevron. Status marks belong to individual threads, including pinned threads and the compact rail.
- Two row rungs, and the rung follows what the ROW renders rather than which group it sits in: 44px when the row actually has a second line, 32px when it is one line plus a relative time — the control rung, the same height as a nav row. A group's disposition (pinned / attention / project) decides whether a subtitle is ALLOWED, not whether one exists — taking the tall rung off the group alone puts a 44px box around 16px of text. Rows centre in whichever rung they take; `min-h-*` alone pins short content to the ceiling.
- Rows keep a fixed title width. The single trailing slot shows the date at rest and the action menu on hover, keyboard focus, or touch. Pinned threads always show a small leading thumbtack. Pin/Unpin lives in the explicit menu; there is no row preview card or hover-only pin button. The menu opens right and groups source links, organisation, and destructive actions. The scrollbar stays outside list controls.
- One chrome pad on PostureBar only (`padding="sm"`, equal on all sides). Features + list sit in a flush-top stack — never a second root `p-2` / Separator under the feature list. Mount PostureBar via `leading`.
- Opening a thread from the dock uses the same list — one source of truth (platform decision 5.1).
- Thread history loads one server cursor page at a time as the reader reaches the end. Keep the explicit Load older conversations control as the keyboard and observer fallback. During title search, only that explicit control continues the bounded search; never preload every thread or its transcript.
- While the initial thread query is pending, keep headers and controls mounted and show skeleton rows in empty list bodies. Empty-state copy appears only after loading settles. Cached rows stay visible during background refresh; loading older pages keeps its separate end control.
- When AppShell collapses the rail (`compact`), features use `SidebarNavItem compact` and thread rows become icon-rail dots with the title on a right Tooltip — never clipped title text inside `w-12`.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
