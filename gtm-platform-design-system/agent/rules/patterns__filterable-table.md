# FilterableTable

A catalog may offer Table and Gallery as two layouts of this same dataset. The surface supplies card content and URL-backed view state; this pattern keeps search, filters, grouping, and paging mounted. Gallery hides Columns, uses one lin...

Import: `@langchain/gtm-platform-design-system/patterns/filterable-table`
Source: `src/patterns/filterable-table.tsx`
System: The GTM Platform internal design system, created by Amal Irgashev.

## Props

### FilterableTable

| Prop | Type | Required |
|---|---|---|
| `columns` | `ColumnDef<TData, unknown>[]` | yes |
| `data` | `TData[]` | yes |
| `getRowId` | `(row: TData, index: number) => string` | yes |
| `title` | `FilterableTableTitle` | no |
| `tabs` | `FilterableTableTabs` | no |
| `search` | `FilterableTableSearch` | no |
| `onReset` | `() => void` | no |
| `serverSorting` | `{` | no |
| `value` | `SortingState` | yes |
| `columnIds` | `readonly string[]` | yes |
| `onChange` | `(value: SortingState) => void` | yes |
| `facets` | `readonly FilterableTableFacet[]` | no |
| `scopes` | `readonly FilterableTableScope[]` | no |
| `filters` | `FilterableTableFilters` | no |
| `groupBy` | `FilterableTableGroupBy` | no |
| `serverGrouping` | `FilterableTableServerGrouping` | no |
| `gallery` | `{` | no |
| `view` | `"table" \| "gallery"` | yes |
| `onViewChange` | `(view: "table" \| "gallery") => void` | yes |
| `renderItem` | `(item: TData) => ReactNode` | yes |
| `actions` | `ReactNode` | no |
| `fill` | `boolean` | no |
| `infinite` | `FilterableTableInfinite` | no |
| `cursorPagination` | `FilterableTableCursorPagination` | no |

### FilterableTableGroupRow

| Prop | Type | Required |
|---|---|---|
| `count` | `number` | yes |
| `expanded` | `boolean` | yes |
| `label` | `ReactNode` | yes |
| `onToggle` | `() => void` | yes |
| `testId` | `string` | no |

### FilterableTableGroupTag

| Prop | Type | Required |
|---|---|---|
| `icon` | `Glyph` | no |
| `label` | `string` | yes |

## The decisions this carries

- A catalog may offer Table and Gallery as two layouts of this same dataset. The surface supplies card content and URL-backed view state; this pattern keeps search, filters, grouping, and paging mounted. Gallery hides Columns, uses one link per card, and keeps each card's metadata below its preview. Server groups retain their exact counts and one expanded child page.
- This is the one table composition in the product: `useAppTable` for the engine, the ReUI grid for the body, this toolbar for everything a user does to it. A surface that needs a table renders FilterableTable and passes columns.
- Reaching for DataGridTable, DataGridContainer or `useReactTable` directly inside a surface is a violation, not an optimisation. A table this pattern cannot express is a gap in this pattern: grow it here, once, for everybody.
- One dataset gets one primary grid. Grouping, filters, saved views, column visibility, and paging are modes of this table, not reasons to build a parallel summary table. A second visual is valid only when it answers a different user decision, and that decision belongs in the surface rule block before the visual ships.
- The toolbar arrangement is fixed. A table that is its section's whole body names the section with `title`, first on the left of this row and on the same line as the controls: a heading stacked above a toolbar spends a row on one word. Search sits left, after the title. Reset appears only while search or a filter is set. Filters, Group by, the selection count and Columns sit right. Group by is the same command popover as Filters: an outline trigger, then each column with its header icon. Applied Filters values render as Quiet badges under that row, using the cell tone when the option has one, with a `md` gap above them so they are not flush with the 32px controls. The selected grouping path may sit there too as a Quiet info badge (`groupBy.tag`): that is the saved grouping, not a filter, and Reset does not clear it. A surface whose visible columns are fixed passes `columnVisibilityControl={false}` and omits Columns.
- The toolbar is immediately adjacent to the grid it changes. Do not move table filters into the page masthead, a global band, or a sibling dashboard block. At narrow widths, use the command popovers and OverflowTabs rather than horizontal toolbar scroll.
- A cursor-paged or `rowCount` table is always in server mode: search and the Filters popover write request state, and the table renders the page the server returned. It never filters mounted rows in the browser. Facets and `search.columnId` remain only for small fully-loaded tables that never page.
- A server-mode filter, search, or scope change keeps this toolbar mounted with the values just written. Only the grid body becomes a table-shaped skeleton (`loading`) until the new page arrives. Never unmount FilterableTable for a query-key change, and never swap the page for a full-pane blob. `isFetchingNextPage` is the existing footer row, not this. Surfaces pass `isServerListLoading` (`isPending` or `isPlaceholderData`).
- The Filters control is the ReUI command popover: one trigger, then each column and its control. A column whose filter is a set takes `type: "multiselect"`: the list stays open, each value toggles, and the applied values render as one Quiet pill EACH, every one removable on its own. The trigger counts values, not fields. Option lists are closed vocabularies passed by the surface, never unique values taken off the current page; an option may carry the server's count for that value, which sits quietly after its label. A leftover scope dropdown is the same request state wearing old clothes; new surfaces use the popover. Account is one of those columns (`type: catalog`). Opening it shows My / All as tabs on that field, then the roster with the same option glyphs as the other selects. MY is the default applied filter: it counts on the trigger and renders as a Quiet pill. ALL does not. A standing two-position scope that is not an Account picker can still be `filters.tabs` at the top of the popover.
- The control row is one height and that height is 32px, the `control` rung: placement picks the size, and this row is a toolbar. Everything standing in it (the search field, Filters, Group by, Reset, Columns) is `control`, never `compact`. Applied Filters sit under it as Quiet info badges, not as a second 32px strip. The 28px rung belongs to the pagination strip below the grid and to controls sitting inside a row or a card.
- When `fill` is set, the grid body is the scroll surface: toolbar and pagination stay put, and the header sticks. Do not wrap a filled table in a pane that also scrolls — SplitView takes `workScroll="contained"` for that host. A filled table is the pane: no PageFrame `py-6` around it. A filled client table with more than VIRTUALIZE_ABOVE_ROWS rows on the page windows them to the viewport on its own (the adoption grid froze at 301 fully mounted rows); a table with expand-as-rows or server grouping never does, since neither is virtualizable.
- Rows are addressed by `getRowId`, which returns the server id. Selection state, SSE invalidation and URL state all key off it, and an array index goes stale the moment a row moves.
- Row selection is a prop, not a column a caller hand builds. Every selectable table in the product gets the same leading checkbox column, the same 44px width, and the same 'n of m selected' readout.
- A row click is also a prop. The grid marks the open leaf (`selectedRowId`) and ignores clicks on group rows. When only some flat rows act, `isRowClickable` removes the pointer and handler from the rest rather than advertising a no-op.
- A live list may mark the rows that just arrived or just changed with `isRowFresh`. A fresh row is tinted for one paint and fades back on the one sanctioned duration, so a reader watching the list sees what moved without a toast or a badge. The surface owns what fresh means and clears it after the paint; the table only draws it. It is a flash, never a state: nothing stays highlighted, and reduced motion cuts the fade.
- Columns resize through the ReUI grid (`tableLayout.columnsResizable`). The select column does not. Resize commits on release so a drag does not re-render every row. Typed cells (`SchemaCell`) live in the column width: they truncate, they do not wrap out of the cell. Every column opens wide enough for its header glyph, title, and sort control (120 is only the floor), so a title is not cropped on first paint. A table that does not fit the pane scrolls sideways. Users can still resize, but never below that header-safe min.
- Grouping is a path the Group by command popover names: one column, or a nest the surface listed (`then`). No grouping is a real position. Changing the path seeds the first leaf-path open (the first row's group at each level): a table that opens fully collapsed shows the reader nothing about a leaf, and one that opens fully expanded has thrown the grouping away. A group row is a row in the same grid, with the count in that row, not a heading outside it. Nested groups indent in that same grid. The group label always starts at the leftmost data column, never under the grouped column, and inherits that column's pinning while the grid scrolls. Identity is `GroupRowContent`: chevron, that field's SchemaCell (`renderGroupLabel`) or a Quiet badge with the column glyph, then the compact count immediately after the badge. The count does not pin to the trailing edge of a wide first column. Aggregates in the other cells use the same cell type as the leaves: money stays money, with the $.
- Server sorting is controlled request state. Only explicitly supported columns expose a sort menu, and changing direction starts a new cursor chain. Reset clears search and filters atomically and cancels any pending search debounce.
- Server mode arrives by passing `rowCount` or `infinite`: `useAppTable` switches sorting, filtering and pagination to manual and the client row models go inert. That is a prop, never a reason to fork the pattern into a second composition.
- A cursor-paged list takes one of two modes, and both are server mode. `infinite` accumulates pages in the query cache and loads the next page when the grid sentinel intersects: no numbered footer, no Load more button. `cursorPagination` pages explicitly, for a list that lives in a bounded scroll area (`fill` in a host of fixed height) and must not grow the page: previous and next on the footer, no page numbers and no page sizes, the readout counting from the page's `offset`, and the surface holding the chain of cursors so a filter change starts it again. A cursor page does not know the total, so the readout ends in a `+`; pass `total` when a sibling read counted the whole set and it reads `1 to 50 of 1,234` instead, with `note` for a short clause about the order. One readout, in the footer where the controls that move it are, never repeated under the table. `infinite` has no footer to put it in, so a surface that scrolls says how much has arrived and of how much in its own line under the grid, and keeps that line honest as pages land. Pass `infinite` for a flat list that grows with the page. A grouped table uses the same sentinel for the open group's child cursor and for the parent group list. Collapsing a group unmounts its child sentinel. Stop asking past the wiki 01 cap (200 mounted rows); past that the grid virtualizes.
- `emptyMessage` may be a node, so a surface's EmptyState, or its failure, sits inside the grid under the toolbar rather than replacing the table and taking its title and controls with it.
- Server search writes the URL after `SEARCH_DEBOUNCE_MS` so history is not a keystroke log. The Query hook that sends `q` waits that same clock before the request. Do not mint a third wait, and do not declare a local `SEARCH_DEBOUNCE_MS`.
- When a surface inspects a row in a SplitView reference pane, pass `frame` so the toolbar spans the split and the grid stays in the work pane. Leaving the toolbar in the table column is how Columns wraps onto a second row while the inspector sits idle beside it. Accounts uses `filterableTableSplitFrame` for that composition. Alerts is list + work, not this frame. Do not copy the toolbar-above-split JSX per surface. A book summary that sits above the named work table (the Opportunities pipeline matrix) is a sibling of that full-page table host, not a child of this fill column. Putting it inside `h-full` leaves only a sliver for the grid.
- A name column that must stay visible while the grid scrolls sideways declares `meta.pin: "left"` (or `"right"`). This pattern seeds pinning from that meta. The header pin control can still unpin. Product routes never call DataGrid pin APIs.
- A parent row may disclose a nested grain when `getRowCanExpand` is set. That slot is not a second FilterableTable and not Group by. When the nested grain shares measures with the parent, set `meta.expandedRows` to `ExpandedRow` cells keyed by parent column id so Sent, Replies, and the rest sit in those columns and the name sits in the pinned name column. Expand-as-rows is not virtualized; do not turn it on past VIRTUALIZE_ABOVE_ROWS. `meta.expandedContent` still fills one colspan cell. Row click toggles that disclosure. Product routes do not import DataGrid expand internals.

These are the shipped rules, read from the source's own exported rule
array. A product question they do not answer is a design decision: raise
it rather than answering it at the call site.
