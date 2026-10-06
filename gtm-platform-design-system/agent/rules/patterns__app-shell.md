# AppShell

The header is earned, not permanent. The sidebar already says where you are, so chrome above the content has to justify itself on every route rather than being inherited from the layout.

Import: `@langchain/gtm-platform-design-system/patterns/app-shell`
Source: `src/patterns/app-shell.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### AppShellBase

| Prop | Type | Required |
|---|---|---|
| `children` | `ReactNode` | yes |
| `contentWidth` | `AppShellContentWidth` | no |
| `contentScroll` | `AppShellContentScroll` | no |
| `chrome` | `AppShellChrome` | no |

### NavigationRail

| Prop | Type | Required |
|---|---|---|
| `sidebar` | `ReactNode` | no |
| `railHeader` | `ReactNode` | no |
| `railFooter` | `ReactNode` | no |
| `railCollapsed` | `boolean` | no |

### FlatShell

| Prop | Type | Required |
|---|---|---|
| `mode` | `"flat"` | yes |
| `header` | `ReactNode` | no |
| `dock` | `ReactNode` | no |

### LineageShell

| Prop | Type | Required |
|---|---|---|
| `mode` | `"lineage"` | yes |
| `band` | `ReactNode` | yes |

### FocusShell

| Prop | Type | Required |
|---|---|---|
| `mode` | `"focus"` | yes |
| `threadRail` | `ReactNode` | yes |
| `band` | `ReactNode` | no |
| `railHeader` | `ReactNode` | no |
| `railFooter` | `ReactNode` | no |
| `railCollapsed` | `boolean` | no |

## The decisions this carries

- The header is earned, not permanent. The sidebar already says where you are, so chrome above the content has to justify itself on every route rather than being inherited from the layout.
- Mode A, flat: Overview, Inbox, Accounts, Campaigns. One 44px local toolbar. No global header of verbs/filters — those stay on the page as PageBand. Product AppChrome may mount a flat `header` slot naming the current destination (AppHeader); that slot is location chrome, not a second band for actions.
- Mode B, lineage: any object route. The header appears only because the sidebar cannot express depth. It carries back, lineage, and the object action, and nothing else.
- Mode C, focus: agent full screen. The sidebar becomes the thread rail. The navigation rail is not hidden in this mode, it is replaced, which is why the two live in different props. The product brand row (`railHeader` / AppRailBrand) stays mounted above the thread rail, and `railFooter` keeps Ask + identity/settings under the list — same person chrome as Home.
- Product rail collapse is shell geometry (`railCollapsed`): open column is 246–400px (default 246, the designed Primary Sidebar) and collapses to the 48px icon rail. Drag the right seam to resize; the brand chevron still collapses. Pane stays mounted — flat and focus both honour it. Brand + collapse live in `railHeader`; identity foot in `railFooter` (flat, lineage, and focus). Focus pins the same AppRailFoot under the thread list so Settings stays reachable. Never vanish the rail on a product route; never collapse SidebarNav items. A shell with no sidebar, header, or footer mounts no rail — destinations earn the column. Design fixtures that are judging a page, not the chrome, may omit them.
- Scroll ownership is explicit via `contentScroll` on THIS primitive — diagnose here, not in page demos. `shell` (default for flat / lineage) wraps children in the content ScrollArea with the route gutter. `host` (default for focus; required whenever children are SplitView or ChatInterface) is a height-bounded clip host with a flush measure. Wrapping those surfaces in the shell ScrollArea collapses `h-full` to content height, so panes pile at the top of an empty viewport instead of filling the column.
- Focus defaults to `host`: ChatInterface docks the composer to the page bottom and scrolls the thread inside. The thread rail is also a clip host; AgentThreadRail owns its list ScrollArea.
- Any AppShell that mounts SplitView must pass `contentScroll="host"`. Enforced by ESLint `gtm/design-law/app-shell-host-scroll`. Never paper over a missing host with page-local height, sticky composer, or a full-width strip above the split.
- Zero, one, or one. Never two. Between the shell and the page there is at most one 44px band on any route: a page in lineage or focus mode does not also render a toolbar band, and filters fuse into whichever band already exists. Flat AppHeader and lineage/focus PageBand never stack.
- Page title: deleted as a *content* masthead on top-level pages. The flat AppHeader may repeat the nav label as location chrome when the rail is collapsed or for orientation; it is not PageMasthead and spends no `text-page` rung.
- Command search: moves to Cmd-K only. A field used by shortcut should not park 320px of chrome on every page in the product.
- Contextual action: moves into the page toolbar, beside the filters it acts on. An action floating in a global header is further from the rows it changes than the filter that selected them.
- Posture control: the sanctioned Home/Agent toggle is the segmented switch in the sidebar (every Core 5 surface board). Keep ⌘J. A floating duplicate in a global header stays forbidden. (`docs/plan/gtm-agent-product/surface-decisions-2026-08-10.md` §2.)
- The band region is 44px whether it holds a lineage band, a toolbar, or the flat AppHeader, and the shell reserves it rather than letting the band measure itself. The whole point of the decision is that the first row of content does not move as a rep walks from Accounts to an account to a campaign.
- Chrome `inset` is the product default (Home, Agent, and every AppShell mount): desk mat behind the rail; flush `shell` work column — no outer pad, radius, or hairline on the card; rail keeps `pt-2`. Shell-scrolled work (no band) matches that `pt-2` so PageMasthead / reading columns share the brand's top air. When a dock sibling is present, the air stays on the work column — the companion header is flush to the card. **`contentScroll="host"` cards stay flush** — SplitView / ChatInterface fill the column edge-to-edge; list title air lives on `ListSidebarChrome`, never as a gap above the resize handle. Pass `chrome="bleed"` only for gallery miniatures that must show the old full-bleed canvas.
- Width is a property of the route, chosen from a closed set of four, never a class at a call site. `contentWidth` takes `reading` 768, `work` 1280 (the default), `wide` (no cap), or `interstitial` 320 for auth and empty-shell routes, and there is no fifth value and no escape hatch. Four because Primer ships four `containerWidth` values and Polaris ships three, and nobody who has run a real product ships one. (Evidence 5.3 rule 2.)
- Pick the measure by job, not taste: `reading` for a bounded reading/settings column; `work` for Overview's peer briefing panels and a single readable list; `wide` for splits/rosters/grids that fill the pane; `interstitial` for auth. Product AppChrome's `SHELL_GEOMETRY` is the only place a route picks one — never pad the sides on the page.
- Centred, everywhere. Amal, 2026-08-05, overruling the evidence's own region-count heuristic: on a route that already has a rail or a dock, the work pane's content centres within the width the pane leaves rather than flushing left. There is no left-anchored value, and the anchor is not a prop, because an anchor that varies by route is a second decision every builder would have to make again. (Evidence 5.3 rule 4 as amended; the fork it settles is section 4.2, Fork 3.)
- The reading measure is 768 and it is the same 768 PageFrame already is. `contentWidth='reading'` and `max-w-3xl` are one number by construction (`--gtm-container-reading`), so a PageFrame route inherits the cap once, never twice. The agent thread column keeps its board-measured 704 as a documented exception with a stated reason, not as a fifth rung. (Primer `medium` 768; Tailwind `max-w-3xl`.)
- Side padding is 16, stepping to 24 at the large breakpoint, and there is no third step: `px-4 lg:px-6` on the measure at every one of the four values, `wide` included. (Primer's stated table; shadcn's own `px-4 lg:px-6`; Material's 24dp at medium. Nothing in the pass supports 32 as a page margin.)
- `wide` drops the cap and keeps the padding, and it is the only value that drops anything. It is for content wider than any measure -- a data grid, a stage matrix -- and it arrives with an obligation: the wide thing scrolls inside its own container. The page never grows its own horizontal scrollbar. That is not taste: the CORE 07 sticky-first-column contract is defined against the scroll container, so a page-level scrollbar sticks the column to the wrong element and the first column drifts. (Canada.ca states the escape as policy; evidence 5.3 rule 6.)
- Geometry is declared as custom properties on the shell root, so anything mounted inside can read the measure it is sitting in rather than being told twice. `--content-width` is the first of that family and joins `--sidebar-width` and `--band-height` rather than starting a parallel system. (Evidence 5.3 rule 8, independently confirmed by shadcn's reference block setting `--sidebar-width` and `--header-height` on its provider.)
- The agent companion is the shell's third region, not a SplitView pane. Flat mode always keeps a dock sibling beside the work column so opening it does not remount the page or break host-scroll height. The slot is width 0 when hidden; when open it takes the session width (default 420). The live AgentSurface fills that slot. Floating does not take this slot.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
