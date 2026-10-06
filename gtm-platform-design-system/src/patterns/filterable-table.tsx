"use client";

import { z } from "zod";
import { useRecoveryState, useRecoveryPathname } from "../lib/browser-recovery";

/*
 * Rules for FilterableTable.
 *
 * The rules themselves are `FILTERABLE_TABLE_RULES` below, not this comment.
 * `/design` renders that array verbatim beside the live pattern, so the
 * composition a builder reads in the gallery and the composition this file
 * assembles are the same strings.
 *
 * This is the organism tier: the decision it carries is that a table surface
 * has exactly one shape. `useAppTable` for the engine, the vendored ReUI grid
 * for the body, and this toolbar row for everything a user does to it. The
 * arrangement is not a starting point to be rearranged per surface, it is the
 * arrangement.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Dispatch, ReactNode, SetStateAction } from "react";
import type {
  ColumnDef,
  ColumnFiltersState,
  ColumnPinningState,
  ColumnSizingState,
  ExpandedState,
  GroupingState,
  PaginationState,
  RowSelectionState,
  SortingState,
  Table,
  Updater,
  VisibilityState,
} from "@tanstack/react-table";

import { DataGrid, DataGridContainer } from "../data-grid/data-grid";
import { DataGridColumnFilter } from "../data-grid/data-grid-column-filter";
import type { DataGridColumnFilterOption } from "../data-grid/data-grid-column-filter";
import { DataGridColumnVisibility } from "../data-grid/data-grid-column-visibility";
import { DataGridLoadMoreSentinel } from "../data-grid/data-grid-load-more";
import { DataGridPagination } from "../data-grid/data-grid-pagination";
import { DataGridTableVirtual } from "../data-grid/data-grid-table-virtual";
import {
  DataGridTable,
  DataGridTableServerGrouped,
  DataGridTableRowSelect,
  DataGridTableRowSelectAll,
} from "../data-grid/data-grid-table";
import type { DataGridServerGrouping } from "../data-grid/data-grid-table";
import { SchemaEnum } from "./schema-cell";
import {
  TableFilters,
  TableFilterPills,
  appliedScopeTabs,
  isFilterValueSet,
} from "./table-filters";
import type {
  TableFilterCatalog,
  TableFilterField,
  TableFilterTabs,
  TableFilterValues,
} from "./table-filters";
import {
  SplitView,
  type SplitViewPosture,
} from "./split-view";
import { Badge } from "../ui/badge";
import { Box, Inline, Stack } from "../ui/box";
import { Button } from "../ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "../ui/command";
import { cn } from "../ui/cn";
import { Check, Layers, LayoutGrid, Search, Settings2, Table2, X, type Glyph } from "../ui/glyphs";
import { GroupRowContent } from "../ui/group-row";
import { Icon } from "../ui/icon";
import { Input } from "../ui/input";
import { LABEL_CLASS } from "../ui/label";
import { POPUP_SURFACE_FLUSH } from "../ui/popup-surface";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "../ui/popover";
import { Skeleton } from "../ui/skeleton";
import { ScrollArea } from "../ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../ui/select";
import { Tabs, TabsList, TabsTrigger } from "../ui/tabs";
import { ToggleGroup, ToggleGroupItem } from "../ui/toggle-group";
import { SEARCH_DEBOUNCE_MS } from "../lib/query__search-debounce";
import {
  DEFAULT_COLUMN_MAX_SIZE,
  DEFAULT_COLUMN_MIN_SIZE,
} from "../lib/table__header-safe-column-size";
import { DEFAULT_PAGE_SIZE, useAppTable } from "../lib/table__use-app-table";
import { ROW_HEIGHT, type RowDensity } from "../lib/table__use-app-table";

const FILTERABLE_TABLE_RULES: readonly string[] = [
  "A catalog may offer Table and Gallery as two layouts of this same dataset. The surface supplies card content and URL-backed view state; this pattern keeps search, filters, grouping, and paging mounted. Gallery hides Columns, uses one link per card, and keeps each card's metadata below its preview. Server groups retain their exact counts and one expanded child page.",
  "This is the one table composition in the product: `useAppTable` for the engine, the ReUI grid for the body, this toolbar for everything a user does to it. A surface that needs a table renders FilterableTable and passes columns.",
  "Reaching for DataGridTable, DataGridContainer or `useReactTable` directly inside a surface is a violation, not an optimisation. A table this pattern cannot express is a gap in this pattern: grow it here, once, for everybody.",
  "One dataset gets one primary grid. Grouping, filters, saved views, column visibility, and paging are modes of this table, not reasons to build a parallel summary table. A second visual is valid only when it answers a different user decision, and that decision belongs in the surface rule block before the visual ships.",
  "The toolbar arrangement is fixed. A table that is its section's whole body names the section with `title`, first on the left of this row and on the same line as the controls: a heading stacked above a toolbar spends a row on one word. Search sits left, after the title. Reset appears only while search or a filter is set. Filters, Group by, the selection count and Columns sit right. Group by is the same command popover as Filters: an outline trigger, then each column with its header icon. Applied Filters values render as Quiet badges under that row, using the cell tone when the option has one, with a `md` gap above them so they are not flush with the 32px controls. The selected grouping path may sit there too as a Quiet info badge (`groupBy.tag`): that is the saved grouping, not a filter, and Reset does not clear it. A surface whose visible columns are fixed passes `columnVisibilityControl={false}` and omits Columns.",
  "The toolbar is immediately adjacent to the grid it changes. Do not move table filters into the page masthead, a global band, or a sibling dashboard block. At narrow widths, use the command popovers and OverflowTabs rather than horizontal toolbar scroll.",
  "A cursor-paged or `rowCount` table is always in server mode: search and the Filters popover write request state, and the table renders the page the server returned. It never filters mounted rows in the browser. Facets and `search.columnId` remain only for small fully-loaded tables that never page.",
  "A server-mode filter, search, or scope change keeps this toolbar mounted with the values just written. Only the grid body becomes a table-shaped skeleton (`loading`) until the new page arrives. Never unmount FilterableTable for a query-key change, and never swap the page for a full-pane blob. `isFetchingNextPage` is the existing footer row, not this. Surfaces pass `isServerListLoading` (`isPending` or `isPlaceholderData`).",
  "The Filters control is the ReUI command popover: one trigger, then each column and its control. A column whose filter is a set takes `type: \"multiselect\"`: the list stays open, each value toggles, and the applied values render as one Quiet pill EACH, every one removable on its own. The trigger counts values, not fields. Option lists are closed vocabularies passed by the surface, never unique values taken off the current page; an option may carry the server's count for that value, which sits quietly after its label. A leftover scope dropdown is the same request state wearing old clothes; new surfaces use the popover. Account is one of those columns (`type: catalog`). Opening it shows My / All as tabs on that field, then the roster with the same option glyphs as the other selects. MY is the default applied filter: it counts on the trigger and renders as a Quiet pill. ALL does not. A standing two-position scope that is not an Account picker can still be `filters.tabs` at the top of the popover.",
  "The control row is one height and that height is 32px, the `control` rung: placement picks the size, and this row is a toolbar. Everything standing in it (the search field, Filters, Group by, Reset, Columns) is `control`, never `compact`. Applied Filters sit under it as Quiet info badges, not as a second 32px strip. The 28px rung belongs to the pagination strip below the grid and to controls sitting inside a row or a card.",
  "When `fill` is set, the grid body is the scroll surface: toolbar and pagination stay put, and the header sticks. Do not wrap a filled table in a pane that also scrolls — SplitView takes `workScroll=\"contained\"` for that host. A filled table is the pane: no PageFrame `py-6` around it. A filled client table with more than VIRTUALIZE_ABOVE_ROWS rows on the page windows them to the viewport on its own (the adoption grid froze at 301 fully mounted rows); a table with expand-as-rows or server grouping never does, since neither is virtualizable.",
  "Rows are addressed by `getRowId`, which returns the server id. Selection state, SSE invalidation and URL state all key off it, and an array index goes stale the moment a row moves.",
  "Row selection is a prop, not a column a caller hand builds. Every selectable table in the product gets the same leading checkbox column, the same 44px width, and the same 'n of m selected' readout.",
  "A row click is also a prop. The grid marks the open leaf (`selectedRowId`) and ignores clicks on group rows. When only some flat rows act, `isRowClickable` removes the pointer and handler from the rest rather than advertising a no-op.",
  "A live list may mark the rows that just arrived or just changed with `isRowFresh`. A fresh row is tinted for one paint and fades back on the one sanctioned duration, so a reader watching the list sees what moved without a toast or a badge. The surface owns what fresh means and clears it after the paint; the table only draws it. It is a flash, never a state: nothing stays highlighted, and reduced motion cuts the fade.",
  "Columns resize through the ReUI grid (`tableLayout.columnsResizable`). The select column does not. Resize commits on release so a drag does not re-render every row. Typed cells (`SchemaCell`) live in the column width: they truncate, they do not wrap out of the cell. Every column opens wide enough for its header glyph, title, and sort control (120 is only the floor), so a title is not cropped on first paint. A table that does not fit the pane scrolls sideways. Users can still resize, but never below that header-safe min.",
  "Grouping is a path the Group by command popover names: one column, or a nest the surface listed (`then`). No grouping is a real position. Changing the path seeds the first leaf-path open (the first row's group at each level): a table that opens fully collapsed shows the reader nothing about a leaf, and one that opens fully expanded has thrown the grouping away. A group row is a row in the same grid, with the count in that row, not a heading outside it. Nested groups indent in that same grid. The group label always starts at the leftmost data column, never under the grouped column, and inherits that column's pinning while the grid scrolls. Identity is `GroupRowContent`: chevron, that field's SchemaCell (`renderGroupLabel`) or a Quiet badge with the column glyph, then the compact count immediately after the badge. The count does not pin to the trailing edge of a wide first column. Aggregates in the other cells use the same cell type as the leaves: money stays money, with the $.",
  "Server sorting is controlled request state. Only explicitly supported columns expose a sort menu, and changing direction starts a new cursor chain. Reset clears search and filters atomically and cancels any pending search debounce.",
  "Server mode arrives by passing `rowCount` or `infinite`: `useAppTable` switches sorting, filtering and pagination to manual and the client row models go inert. That is a prop, never a reason to fork the pattern into a second composition.",
  "A cursor-paged list takes one of two modes, and both are server mode. `infinite` accumulates pages in the query cache and loads the next page when the grid sentinel intersects: no numbered footer, no Load more button. `cursorPagination` pages explicitly, for a list that lives in a bounded scroll area (`fill` in a host of fixed height) and must not grow the page: previous and next on the footer, no page numbers and no page sizes, the readout counting from the page's `offset`, and the surface holding the chain of cursors so a filter change starts it again. A cursor page does not know the total, so the readout ends in a `+`; pass `total` when a sibling read counted the whole set and it reads `1 to 50 of 1,234` instead, with `note` for a short clause about the order. One readout, in the footer where the controls that move it are, never repeated under the table. `infinite` has no footer to put it in, so a surface that scrolls says how much has arrived and of how much in its own line under the grid, and keeps that line honest as pages land. Pass `infinite` for a flat list that grows with the page. A grouped table uses the same sentinel for the open group's child cursor and for the parent group list. Collapsing a group unmounts its child sentinel. Stop asking past the wiki 01 cap (200 mounted rows); past that the grid virtualizes.",
  "`emptyMessage` may be a node, so a surface's EmptyState, or its failure, sits inside the grid under the toolbar rather than replacing the table and taking its title and controls with it.",
  "Server search writes the URL after `SEARCH_DEBOUNCE_MS` so history is not a keystroke log. The Query hook that sends `q` waits that same clock before the request. Do not mint a third wait, and do not declare a local `SEARCH_DEBOUNCE_MS`.",
  "When a surface inspects a row in a SplitView reference pane, pass `frame` so the toolbar spans the split and the grid stays in the work pane. Leaving the toolbar in the table column is how Columns wraps onto a second row while the inspector sits idle beside it. Accounts uses `filterableTableSplitFrame` for that composition. Alerts is list + work, not this frame. Do not copy the toolbar-above-split JSX per surface. A book summary that sits above the named work table (the Opportunities pipeline matrix) is a sibling of that full-page table host, not a child of this fill column. Putting it inside `h-full` leaves only a sliver for the grid.",
  "A name column that must stay visible while the grid scrolls sideways declares `meta.pin: \"left\"` (or `\"right\"`). This pattern seeds pinning from that meta. The header pin control can still unpin. Product routes never call DataGrid pin APIs.",
  "A parent row may disclose a nested grain when `getRowCanExpand` is set. That slot is not a second FilterableTable and not Group by. When the nested grain shares measures with the parent, set `meta.expandedRows` to `ExpandedRow` cells keyed by parent column id so Sent, Replies, and the rest sit in those columns and the name sits in the pinned name column. Expand-as-rows is not virtualized; do not turn it on past VIRTUALIZE_ABOVE_ROWS. `meta.expandedContent` still fills one colspan cell. Row click toggles that disclosure. Product routes do not import DataGrid expand internals.",
];

/** The leading checkbox column, identical on every selectable table. */
const SELECTION_COLUMN_ID = "select";

/**
 * Past this many rows on one page, a filled table mounts only the rows in its viewport. Below it,
 * every row mounts, which is what jsdom and the small fixtures see, and what keeps a short list free
 * of spacer rows.
 */
const VIRTUALIZE_ABOVE_ROWS = 200;

/** Search field width: wide enough for an account name, short enough to leave the facets room. */
const SEARCH_FIELD_CLASS = "w-56 pl-8";

const SEARCH_ICON_CLASS = "pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2";

/** The Group by value that means the table is flat. Not a column id. */
const GROUP_NONE = "none";

/** Joins a grouping path for the popover. Matches TanStack's nested group ids. */
const GROUP_PATH_SEP = ">";

const GROUP_POPOVER_CLASS = "w-72";

const ITEM_CHECK_CLASS =
  "pointer-events-none absolute right-2 flex items-center justify-center";

const SCOPE_TRIGGER_CLASS = "w-44";

const LOAD_MORE_SKELETON_CLASS = "h-row-data w-full rounded-none";

const SEARCH_RESULTS_CLASS = cn(
  "absolute top-full left-0 z-50 mt-1.5 w-80 overflow-hidden",
  POPUP_SURFACE_FLUSH
);

function TableSearchField({
  onChange,
  search,
  value,
}: {
  onChange: (next: string) => void;
  search: FilterableTableSearch;
  value: string;
}) {
  const results = search.results;
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const label = search.placeholder ?? "Search";

  useEffect(() => {
    if (!open || results === undefined) return;
    function onPointerDown(event: PointerEvent): void {
      const root = rootRef.current;
      if (
        root === null ||
        (event.target instanceof Node && root.contains(event.target))
      ) {
        return;
      }
      setOpen(false);
    }
    function onKey(event: KeyboardEvent): void {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, results]);

  const field = (
    <>
      <Box className={SEARCH_ICON_CLASS}>
        <Icon icon={Search} size="sm" className="text-ink-subtle" />
      </Box>
      <Input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onFocus={() => {
          if (results !== undefined) setOpen(true);
        }}
        placeholder={label}
        aria-label={label}
        maxLength={search.maxLength}
        aria-expanded={results === undefined ? undefined : open}
        aria-controls={
          results === undefined || !open ? undefined : "table-search-results"
        }
        aria-autocomplete={results === undefined ? undefined : "list"}
        className={SEARCH_FIELD_CLASS}
      />
    </>
  );

  if (results === undefined) {
    return <Box className="relative">{field}</Box>;
  }

  const emptyLabel = results.emptyLabel ?? "No matches.";
  const pendingEmpty = results.pending === true && results.items.length === 0;

  return (
    <Box render={<div ref={rootRef} />} className="relative">
      {field}
      {open ? (
        <Box
          id="table-search-results"
          role="listbox"
          aria-label={label}
          className={SEARCH_RESULTS_CLASS}
        >
          <Command shouldFilter={false} className="rounded-none">
            <CommandList>
              {pendingEmpty ? (
                <p className="px-2.5 py-8 text-center text-label text-ink-subtle">
                  Searching…
                </p>
              ) : results.items.length === 0 ? (
                <p className="px-2.5 py-8 text-center text-label text-ink-subtle">
                  {emptyLabel}
                </p>
              ) : (
                <CommandGroup>
                  {results.items.map((item) => (
                    <CommandItem
                      key={item.id}
                      value={item.id}
                      onSelect={() => {
                        results.onSelect(item.id);
                        setOpen(false);
                      }}
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-label font-medium">
                          {item.label}
                        </span>
                        {item.description === undefined ||
                        item.description.length === 0 ? null : (
                          <span className="block truncate text-meta text-ink-subtle">
                            {item.description}
                          </span>
                        )}
                      </span>
                    </CommandItem>
                  ))}
                </CommandGroup>
              )}
              {results.hasMore === true && results.onFetchMore !== undefined ? (
                <DataGridLoadMoreSentinel enabled onVisible={results.onFetchMore} />
              ) : null}
            </CommandList>
          </Command>
        </Box>
      ) : null}
    </Box>
  );
}

function applyUpdater<TValue>(
  updater: Updater<TValue>,
  previous: TValue
): TValue {
  return typeof updater === "function"
    ? (updater as (old: TValue) => TValue)(previous)
    : updater;
}

function toggleExpandedRow<TData>(table: Table<TData>, rowId: string): void {
  const row = table.getRowModel().rows.find((entry) => entry.id === rowId);
  row?.toggleExpanded();
}

function noopSearch(): void {}

interface DebouncedSearchState {
  source: string;
  draft: string;
}

function useDebouncedSearch(
  value: string,
  onChange: (next: string) => void,
  delay: number = SEARCH_DEBOUNCE_MS
): [string, (next: string) => void, () => void] {
  const [draftState, setDraftState] = useState<DebouncedSearchState>({
    source: value,
    draft: value,
  });
  const onChangeRef = useRef(onChange);
  const timeoutRef = useRef<number | null>(null);
  const draft = draftState.source === value ? draftState.draft : value;

  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  useEffect(() => {
    if (timeoutRef.current !== null) {
      window.clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, [value]);

  useEffect(() => {
    return () => {
      if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
    };
  }, []);

  function change(next: string): void {
    setDraftState({ source: value, draft: next });
    if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
    timeoutRef.current = window.setTimeout(() => {
      timeoutRef.current = null;
      onChangeRef.current(next);
    }, delay);
  }

  function reset(): void {
    if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
    timeoutRef.current = null;
    setDraftState({ source: value, draft: "" });
  }

  return [draft, change, reset];
}

function selectionColumn<TData extends object>(): ColumnDef<TData, unknown> {
  return {
    cell: ({ row }) => <DataGridTableRowSelect row={row} />,
    enableGrouping: false,
    enableHiding: false,
    enableResizing: false,
    enableSorting: false,
    header: () => <DataGridTableRowSelectAll />,
    id: SELECTION_COLUMN_ID,
    meta: {
      headerTitle: "Select",
      skeleton: <Skeleton className="size-4 rounded-compact" />,
    },
    size: 44,
  };
}

/** The value a column would group this row under. */
function groupingValue<TData>(
  row: TData,
  columnId: string,
  columns: readonly ColumnDef<TData, unknown>[]
): string {
  const column = columns.find((entry) => entry.id === columnId);
  if (column !== undefined && "accessorFn" in column && column.accessorFn !== undefined) {
    return String(column.accessorFn(row, 0) ?? "");
  }
  if (
    column !== undefined &&
    "accessorKey" in column &&
    column.accessorKey !== undefined
  ) {
    const value = (row as Record<string, unknown>)[String(column.accessorKey)];
    return value == null ? "" : String(value);
  }
  const value = (row as Record<string, unknown>)[columnId];
  return value == null ? "" : String(value);
}

function optionGrouping(option: FilterableTableGroupOption): string[] {
  return option.then === undefined
    ? [option.columnId]
    : [option.columnId, ...option.then];
}

function groupingPathKey(grouping: readonly string[]): string {
  return grouping.length === 0 ? GROUP_NONE : grouping.join(GROUP_PATH_SEP);
}

function columnPinningFromMeta<TData>(
  columns: readonly ColumnDef<TData, unknown>[]
): ColumnPinningState {
  const left: string[] = [];
  const right: string[] = [];
  for (const column of columns) {
    const id = column.id;
    const pin = column.meta?.pin;
    if (id === undefined) continue;
    if (pin === "left") left.push(id);
    if (pin === "right") right.push(id);
  }
  return { left, right };
}

/** Seed the first leaf-path open when the grouping path changes. */
function seedFirstGroupExpanded<TData>(
  grouping: GroupingState,
  data: readonly TData[],
  columns: readonly ColumnDef<TData, unknown>[]
): ExpandedState {
  const first = data[0];
  if (first === undefined || grouping.length === 0) return {};
  const expanded: Record<string, boolean> = {};
  let parentId = "";
  for (const columnId of grouping) {
    const part = `${columnId}:${groupingValue(first, columnId, columns)}`;
    const id = parentId.length === 0 ? part : `${parentId}${GROUP_PATH_SEP}${part}`;
    expanded[id] = true;
    parentId = id;
  }
  return expanded;
}

/** The slices of table state this pattern owns, controlled together or not at all. */
const booleanMap = z.record(z.string().max(512), z.boolean());
const tablePreferencesSchema = z.object({ columnVisibility: booleanMap, columnSizing: z.record(z.string().max(512), z.number().finite().min(0).max(10000)) });
const tableRecoverySchema = tablePreferencesSchema.extend({
  sorting: z.array(z.object({ id: z.string(), desc: z.boolean() })).max(100),
  columnFilters: z.array(z.object({ id: z.string(), value: z.unknown() })).max(100),
  rowSelection: booleanMap, pagination: z.object({ pageIndex: z.number().int().min(0), pageSize: z.number().int().min(1) }),
  grouping: z.array(z.string()).max(20), expanded: z.union([z.literal(true), booleanMap]),
});
interface FilterableTableState {
  sorting: SortingState;
  columnFilters: ColumnFiltersState;
  columnVisibility: VisibilityState;
  columnSizing: ColumnSizingState;
  rowSelection: RowSelectionState;
  pagination: PaginationState;
  grouping: GroupingState;
  expanded: ExpandedState;
}

interface FilterableTableSearchResult {
  id: string;
  label: string;
  description?: string;
}

interface FilterableTableSearchResults {
  items: readonly FilterableTableSearchResult[];
  onSelect: (id: string) => void;
  pending?: boolean;
  hasMore?: boolean;
  onFetchMore?: () => void;
  emptyLabel?: string;
}

interface FilterableTableSearch {
  placeholder?: string;
  /**
   * Client search: TanStack filters this column on the mounted rows.
   * Omit on server-paged lists; pass `value` + `onChange` instead.
   */
  columnId?: string;
  /** Server search: the request value. Pair with `onChange`. */
  value?: string;
  onChange?: (value: string) => void;
  /** Debounce for server search before `onChange` fires. Default 300ms. */
  debounceMs?: number;
  /** The longest search the server accepts. The field stops there, so what it shows is what is sent. */
  maxLength?: number;
  /**
   * Finder popover under the field. First page comes from the list cache;
   * scrolling asks for the next cursor. Selecting does not write `q` on the grid.
   */
  results?: FilterableTableSearchResults;
}

interface FilterableTableTab {
  value: string;
  label: string;
}

interface FilterableTableTabs {
  value: string;
  onValueChange: (value: string) => void;
  items: readonly FilterableTableTab[];
  /** Accessible name for the tablist. */
  label: string;
  /** Same glyph the grid header uses for this column. */
  icon?: Glyph;
}

interface FilterableTableFilters {
  fields: readonly TableFilterField[];
  values: TableFilterValues;
  onChange: (id: string, value: string | undefined) => void;
  onClear?: () => void;
  /** Standing My / All when the surface is not using an Account field. */
  tabs?: TableFilterTabs;
  /** Roster for the Account field (`type: catalog`). */
  catalog?: TableFilterCatalog;
  /** Independent remote catalogs keyed by filter field id. */
  catalogs?: Readonly<Record<string, TableFilterCatalog>>;
}

interface FilterableTableFacet {
  columnId: string;
  title: string;
  /** The column needs `filterFn: "arrIncludesSome"` for a multi select facet. */
  options: readonly DataGridColumnFilterOption[];
}

interface FilterableTableGroupOption {
  columnId: string;
  label: string;
  /** Same glyph the grid header uses for this column. */
  icon?: Glyph;
  /** Reuse the grouped field's SchemaCell grammar in server group headers. */
  renderGroupLabel?: (group: { id: string; label: string }) => ReactNode;
  /** Nested keys after `columnId`. Outer first. Omit for a single-level group. */
  then?: readonly string[];
}

function resolveServerGroupLabel(
  option: FilterableTableGroupOption | undefined,
  group: { id: string; labelText: string }
): ReactNode {
  if (option?.renderGroupLabel !== undefined) {
    return option.renderGroupLabel({ id: group.id, label: group.labelText });
  }
  return <SchemaEnum value={group.labelText} icon={option?.icon} />;
}

interface FilterableTableGroupBy {
  /** Grouping paths the Group by control may name, in the order they appear. */
  options: readonly FilterableTableGroupOption[];
  /** Controlled server grouping path. Empty means the ordinary flat list. */
  value?: readonly string[];
  /** Writes a controlled server grouping path to page/query state. */
  onChange?: (value: readonly string[]) => void;
  /** Toolbar noun. Default Group by. */
  label?: string;
  /** Draw the selected path as a Quiet info badge under the toolbar. */
  tag?: boolean;
}

interface FilterableTableScopeOption {
  value: string;
  label: string;
}

interface FilterableTableScope {
  /** Stable id for the control. Not a column id. */
  id: string;
  title: string;
  /** The selected value, or null when the request is unscoped. */
  value: string | null;
  options: readonly FilterableTableScopeOption[];
  onChange: (value: string | null) => void;
}

interface FilterableTableInfinite {
  hasMore: boolean;
  isFetchingMore: boolean;
  onFetchMore: () => void;
  /**
   * Freeze the sentinel. Used while placeholder rows from the previous request
   * are still on screen, and while the last page was empty (the surface's scan
   * effect owns that case). Paging placeholder data is how one filter fans out
   * into a request storm.
   */
  pause?: boolean;
}

type FilterableTableServerGrouping = DataGridServerGrouping;

interface FilterableTableCursorPagination {
  hasPrevious: boolean;
  hasNext: boolean;
  onPrevious: () => void;
  onNext: () => void;
  /** Rows before this page in the cursor chain, so the readout counts from where this page starts. */
  offset: number;
  /**
   * The whole set's size, when a sibling read counted it. Without it the readout ends in a `+`,
   * because a cursor page only knows there is more.
   */
  total?: number;
  /** A short clause after the readout, for the one thing the count does not say: "newest first". */
  note?: string;
}

/**
 * What the footer says about the page on screen: where it starts, where it ends, and either the whole
 * set's size or a `+` for the part a cursor cannot see.
 */
function cursorReadout(
  cursorPagination: FilterableTableCursorPagination,
  rows: number
): string {
  const from = cursorPagination.offset + (rows === 0 ? 0 : 1);
  const to = cursorPagination.offset + rows;
  const span =
    cursorPagination.total === undefined
      ? `${from} - ${to}${cursorPagination.hasNext ? "+" : ""}`
      : `${from} to ${to} of ${cursorPagination.total.toLocaleString("en-US")}`;
  return cursorPagination.note === undefined ? span : `${span}, ${cursorPagination.note}`;
}

/** The section's name, on the toolbar row. */
interface FilterableTableTitle {
  text: string;
  /** Decorative glyph beside the title, as a section title carries one. */
  icon?: Glyph;
}

interface FilterableTableFrame {
  toolbar: ReactNode;
  grid: ReactNode;
  pagination: ReactNode;
}

/** Toolbar above a work+reference catalog. Alerts and Accounts share this. */
function filterableTableSplitFrame(options: {
  posture?: SplitViewPosture;
  referenceHidden: boolean;
  referenceLabel: string;
  referencePane: ReactNode;
  workLabel: string;
  /** Section title for the work table, above its toolbar. */
  workTitle?: string;
  /** Decorative glyph beside `workTitle`. */
  workTitleIcon?: Glyph;
}): (parts: FilterableTableFrame) => ReactNode {
  return function FilterableTableSplitFrame({ toolbar, grid }) {
    return (
    <Stack gap="md" grow className="h-full min-h-0">
      {options.workTitle === undefined ? null : (
        <Inline gap="sm" align="center">
          {options.workTitleIcon === undefined ? null : (
            <Icon icon={options.workTitleIcon} size="md" className="text-ink-subtle" />
          )}
          <Box render={<h2 />} className="text-title font-medium text-ink">
            {options.workTitle}
          </Box>
        </Inline>
      )}
      {toolbar}
      <Box className="min-h-0 flex-1">
        <SplitView
          workLabel={options.workLabel}
          workPane={grid}
          workScroll="contained"
          posture={options.posture}
          referenceLabel={options.referenceLabel}
          referencePane={options.referencePane}
          referenceHidden={options.referenceHidden}
          referenceSeam={false}
          referenceWidth="report"
          referenceScroll="contained"
        />
      </Box>
    </Stack>
    );
  };
}

interface FilterableTableProps<TData extends object> {
  columns: ColumnDef<TData, unknown>[];
  data: TData[];
  /** Stable server id per row. Never the array index. */
  getRowId: (row: TData, index: number) => string;
  /** The section's name, first on the toolbar row. For a table that is its section's whole body. */
  title?: FilterableTableTitle;
  /** Quick filters as a segmented tab strip, left of search. */
  tabs?: FilterableTableTabs;
  /** Omit for a table with no search field. */
  search?: FilterableTableSearch;
  /** One URL update clears all filters and search, preserving grouping and sorting. */
  onReset?: () => void;
  /** Only these columns can order the complete server result before pagination. */
  serverSorting?: {
    value: SortingState;
    columnIds: readonly string[];
    onChange: (value: SortingState) => void;
  };
  /** Faceted multi selects, left to right after the search field. Small client tables only. */
  facets?: readonly FilterableTableFacet[];
  /** Server scopes as leftover dropdowns. New surfaces use `filters`. */
  scopes?: readonly FilterableTableScope[];
  /** Command popover of column filters. Writes request state; does not filter mounted rows. */
  filters?: FilterableTableFilters;
  /** Puts Group by on the right with Filters. Omit to leave the table flat. */
  groupBy?: FilterableTableGroupBy;
  /** Exact server group summaries plus the rows loaded for the one expanded group. */
  serverGrouping?: FilterableTableServerGrouping;
  /** Alternate layout of a catalog, sharing this toolbar and server-paged rows. */
  gallery?: {
    view: "table" | "gallery";
    onViewChange: (view: "table" | "gallery") => void;
    renderItem: (item: TData) => ReactNode;
  };
  /** Surface action after Columns, aligned to the fixed toolbar's right edge. */
  actions?: ReactNode;
  /**
   * The grid body fills the host and scrolls; toolbar and pagination stay put.
   * Pair with a host that does not scroll the table away (`workScroll="contained"`).
   */
  fill?: boolean;
  /**
   * Cursor-paged server list that accumulates in the query cache. The next
   * page loads when the grid sentinel intersects. Hides the numbered footer.
   */
  infinite?: FilterableTableInfinite;
  /**
   * Cursor-paged server list paged explicitly: the grid shows the one page the
   * server returned, with previous and next on the footer and no page numbers.
   */
  cursorPagination?: FilterableTableCursorPagination;
  /**
   * Compose the toolbar, grid and pagination yourself. The default is a
   * stacked column. Alerts uses this to put the toolbar above a SplitView.
   */
  frame?: (parts: FilterableTableFrame) => ReactNode;
  /** Prepends the standard checkbox column and turns on the selection readout. */
  selectable?: boolean;
  pageSize?: number;
  /** Row height rung; drives the virtualizer's estimate. */
  density?: RowDensity;
  /** Server total. Supplying it moves sorting, filtering and paging to the server. */
  rowCount?: number;
  /** What the grid body says with no rows: words, a surface's EmptyState, or its failure. */
  emptyMessage?: ReactNode;
  /**
   * Server-mode first page, or a filter/search/scope change still in flight.
   * Toolbar stays; the grid body is the DataGrid skeleton. Do not unmount
   * this table for a query-key change.
   */
  loading?: boolean;
  /** Accessible name for the busy grid. Default "Loading rows". */
  loadingLabel?: string;
  initialSorting?: SortingState;
  /** Columns that start hidden; the Columns control can show them. Keyed by column id. */
  initialColumnVisibility?: VisibilityState;
  /** When false, the visible set is fixed and Columns stays off the toolbar. */
  columnVisibilityControl?: boolean;
  /** Opens grouped when the control is present. Empty means None. */
  initialGrouping?: GroupingState;
  /** Opens the inspector for this leaf. Group rows never match. */
  selectedRowId?: string;
  onRowClick?: (row: TData) => void;
  /** Limits row-click behavior to rows that actually own an action. */
  isRowClickable?: (row: TData) => boolean;
  /** Rows that just arrived or changed in a live list, tinted for one paint. The surface clears it. */
  isRowFresh?: (row: TData) => boolean;
  /**
   * Lets a parent row disclose `meta.expandedRows` or `meta.expandedContent`.
   * When set, row click toggles that row unless `isRowClickable` rejects it.
   */
  getRowCanExpand?: (row: TData) => boolean;
  /** Controlled state, for surfaces that mirror the table into the URL. */
  state?: FilterableTableState;
  onStateChange?: Dispatch<SetStateAction<FilterableTableState>>;
}

function initialFilterableTableState<TData>(
  pageSize: number,
  sorting: SortingState,
  grouping: GroupingState,
  data: readonly TData[],
  columns: readonly ColumnDef<TData, unknown>[],
  columnVisibility: VisibilityState = {}
): FilterableTableState {
  return {
    columnFilters: [],
    columnSizing: {},
    columnVisibility,
    expanded: seedFirstGroupExpanded(grouping, data, columns),
    grouping,
    pagination: { pageIndex: 0, pageSize },
    rowSelection: {},
    sorting,
  };
}

interface FilterableTableGroupControlItem {
  icon?: Glyph;
  label: string;
  value: string;
}

/**
 * Same command popover as Filters. The table toolbar keeps the outline
 * trigger. List sidebar chrome passes `trigger="icon"` so Group by matches
 * the compact Filters glyph beside the title.
 */
function FilterableTableGroupControl({
  items,
  label,
  value,
  onChange,
  trigger = "button",
}: {
  items: readonly FilterableTableGroupControlItem[];
  label: string;
  value: string;
  onChange: (next: string | null) => void;
  trigger?: "button" | "icon";
}) {
  const [open, setOpen] = useState(false);
  const iconOnly = trigger === "icon";

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            variant={iconOnly ? "ghost" : "outline"}
            size={iconOnly ? "icon-sm" : undefined}
          />
        }
        aria-label={label}
      >
        <Icon icon={Layers} size="sm" />
        {iconOnly ? null : label}
      </PopoverTrigger>
      <PopoverContent
        align="end"
        inset="flush"
        className={GROUP_POPOVER_CLASS}
      >
        <Command>
          <CommandInput placeholder={label} />
          <CommandList>
            <CommandEmpty>Nothing matches.</CommandEmpty>
            <CommandGroup>
              {items.map((item) => {
                const selected = item.value === value;
                return (
                  <CommandItem
                    key={item.value}
                    value={item.label}
                    onSelect={() => {
                      onChange(item.value === GROUP_NONE ? null : item.value);
                      setOpen(false);
                    }}
                    className={selected ? "pr-8" : undefined}
                  >
                    {item.icon === undefined ? null : (
                      <Icon
                        icon={item.icon}
                        size="sm"
                        className="text-ink-subtle"
                      />
                    )}
                    {item.label}
                    {selected ? (
                      <span className={ITEM_CHECK_CLASS}>
                        <Icon icon={Check} size="sm" />
                      </span>
                    ) : null}
                  </CommandItem>
                );
              })}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

interface FilterableTableGroupRowProps {
  count: number;
  expanded: boolean;
  label: ReactNode;
  onToggle: () => void;
  testId?: string;
}

/** Canonical grouped-row geometry for a list pane that is not itself a table. */
function FilterableTableGroupRow({
  count,
  expanded,
  label,
  onToggle,
  testId,
}: FilterableTableGroupRowProps) {
  return (
    <Box
      data-slot="filterable-table-group-row"
      data-testid={testId}
      className="min-h-row-data border-b border-line px-3 py-1 hover:bg-hover"
    >
      <GroupRowContent
        count={count}
        expanded={expanded}
        label={label}
        onToggle={onToggle}
      />
    </Box>
  );
}

interface FilterableTableGroupTagProps {
  icon?: Glyph;
  label: string;
}

/** Applied grouping path, using the same Quiet info treatment as FilterableTable. */
function FilterableTableGroupTag({
  icon,
  label,
}: FilterableTableGroupTagProps) {
  return (
    <Badge tier="quiet" tone="info" data-slot="filterable-table-group-tag">
      {icon === undefined ? null : <Icon icon={icon} size="sm" />}
      <Box render={<span />}>{label}</Box>
    </Badge>
  );
}

/** The table surface: toolbar, grid and pagination as one composition. */
function FilterableTable<TData extends object>({
  columns,
  data,
  getRowId,
  title,
  tabs,
  search,
  facets,
  scopes,
  filters,
  groupBy,
  serverGrouping,
  serverSorting,
  onReset,
  gallery,
  actions,
  fill = false,
  infinite,
  cursorPagination,
  frame,
  selectable = false,
  pageSize = DEFAULT_PAGE_SIZE,
  density = "data",
  rowCount,
  emptyMessage = "Nothing matches those filters",
  loading = false,
  loadingLabel = "Loading rows",
  initialSorting,
  initialColumnVisibility,
  columnVisibilityControl = true,
  initialGrouping,
  selectedRowId,
  onRowClick,
  isRowClickable,
  isRowFresh,
  getRowCanExpand,
  state,
  onStateChange,
}: FilterableTableProps<TData>) {
  const pathname = useRecoveryPathname();
  const tableKey = `table:${pathname}:${columns.map((column) => column.id ?? ("accessorKey" in column ? String(column.accessorKey) : String(column.header))).join(",")}`;
  // The recovered preferences win over the initial state, so the columns a surface hides by default
  // have to be the seed here as well, or a first visit shows every column.
  const [preferences, setPreferences] = useRecoveryState(`${tableKey}:preferences`, { columnVisibility: initialColumnVisibility ?? {}, columnSizing: {} }, tablePreferencesSchema, "local");
  const [internalState, setInternalState] = useRecoveryState<FilterableTableState>(tableKey, () =>
    initialFilterableTableState(
      pageSize,
      initialSorting ?? [],
      initialGrouping ?? [],
      data,
      columns,
      initialColumnVisibility ?? {}
    ), tableRecoverySchema
  );

  const tableState = useMemo(() => state ?? { ...internalState, ...preferences }, [state, internalState, preferences]);
  const updateState = onStateChange ?? setInternalState;
  const setTableState = useCallback<Dispatch<SetStateAction<FilterableTableState>>>((update) => {
    const next = typeof update === "function" ? update(tableState) : update;
    if (next === tableState) return;
    if (next.columnSizing !== tableState.columnSizing || next.columnVisibility !== tableState.columnVisibility) {
      setPreferences({ columnSizing: next.columnSizing, columnVisibility: next.columnVisibility });
    }
    updateState(next);
  }, [updateState, tableState, setPreferences]);
  const groupingEnabled = groupBy !== undefined;
  const serverGroupingEnabled = groupBy?.onChange !== undefined;
  const localGroupingEnabled = groupingEnabled && !serverGroupingEnabled;
  const infiniteScroll = infinite !== undefined;
  /* Explicit cursor paging is server paging too: the grid shows exactly the page it was handed. */
  const serverPaged = infiniteScroll || serverGrouping !== undefined || cursorPagination !== undefined;
  const galleryActive = gallery?.view === "gallery";

  if (infiniteScroll && localGroupingEnabled) {
    throw new Error(
      "Infinite tables require a server grouped read; local groupBy only sees loaded rows."
    );
  }

  useEffect(() => {
    if (!serverPaged) return;
    const nextSize = Math.max(data.length, 1);
    setTableState((previous) => {
      if (
        previous.pagination.pageIndex === 0 &&
        previous.pagination.pageSize === nextSize
      ) {
        return previous;
      }
      return {
        ...previous,
        pagination: { pageIndex: 0, pageSize: nextSize },
      };
    });
  }, [data.length, serverPaged, setTableState]);

  const isServerMode = serverPaged || rowCount !== undefined;
  const sortableColumns = isServerMode
    ? columns.map((column) => ({
        ...column,
        enableSorting: column.enableSorting !== false &&
          serverSorting?.columnIds.includes(column.id ?? "") === true,
      }))
    : columns;
  const gridColumns = selectable
    ? [selectionColumn<TData>(), ...sortableColumns]
    : sortableColumns;

  const { table, recordCount } = useAppTable<TData>({
    columns: gridColumns,
    data,
    density,
    getRowId,
    rowCount,
    manual: serverPaged ? true : undefined,
    state: { ...tableState, ...(serverSorting === undefined ? {} : { sorting: serverSorting.value }) },
    tableOptions: {
      autoResetExpanded: false,
      initialState: {
        columnPinning: columnPinningFromMeta(gridColumns),
      },
      defaultColumn: {
        enableResizing: true,
        maxSize: DEFAULT_COLUMN_MAX_SIZE,
        minSize: DEFAULT_COLUMN_MIN_SIZE,
      },
      enableMultiSort: !isServerMode,
      enableColumnPinning: true,
      enableColumnResizing: true,
      enableGrouping: localGroupingEnabled,
      enableRowSelection: selectable,
      ...(getRowCanExpand === undefined
        ? {}
        : { getRowCanExpand: (row) => getRowCanExpand(row.original) }),
      groupedColumnMode: false,
      onColumnFiltersChange: (updater) =>
        setTableState((previous) => ({
          ...previous,
          columnFilters: applyUpdater(updater, previous.columnFilters),
        })),
      onColumnSizingChange: (updater) =>
        setTableState((previous) => ({
          ...previous,
          columnSizing: applyUpdater(updater, previous.columnSizing),
        })),
      onColumnVisibilityChange: (updater) =>
        setTableState((previous) => ({
          ...previous,
          columnVisibility: applyUpdater(updater, previous.columnVisibility),
        })),
      onExpandedChange: (updater) =>
        setTableState((previous) => ({
          ...previous,
          expanded: applyUpdater(updater, previous.expanded),
        })),
      onGroupingChange: (updater) =>
        setTableState((previous) => {
          const grouping = applyUpdater(updater, previous.grouping);
          return {
            ...previous,
            expanded: seedFirstGroupExpanded(grouping, data, columns),
            grouping,
            pagination: { ...previous.pagination, pageIndex: 0 },
          };
        }),
      onPaginationChange: (updater) =>
        setTableState((previous) => ({
          ...previous,
          pagination: applyUpdater(updater, previous.pagination),
        })),
      onRowSelectionChange: (updater) =>
        setTableState((previous) => ({
          ...previous,
          rowSelection: applyUpdater(updater, previous.rowSelection),
        })),
      onSortingChange: (updater) => {
        if (serverSorting !== undefined) {
          serverSorting.onChange(applyUpdater(updater, serverSorting.value));
          return;
        }
        setTableState((previous) => ({
          ...previous,
          sorting: applyUpdater(updater, previous.sorting),
        }));
      },
    },
  });

  const handleRowClick =
    getRowCanExpand === undefined && onRowClick === undefined
      ? undefined
      : (data: TData) => {
          if (getRowCanExpand?.(data) === true) {
            toggleExpandedRow(table, getRowId(data, 0));
          }
          onRowClick?.(data);
        };

  const searchColumn =
    search?.columnId === undefined
      ? undefined
      : table.getColumn(search.columnId);
  const isServerSearch = search?.onChange !== undefined;
  const [serverSearchDraft, setServerSearchDraft, resetSearchDraft] = useDebouncedSearch(
    search?.value ?? "",
    search?.onChange ?? noopSearch,
    search?.debounceMs ?? SEARCH_DEBOUNCE_MS
  );
  const rawClientSearch = searchColumn?.getFilterValue();
  const clientSearchValue =
    typeof rawClientSearch === "string" ? rawClientSearch : "";
  const searchValue = isServerSearch ? serverSearchDraft : clientSearchValue;
  const hasScopeFilters = (scopes ?? []).some((scope) => scope.value != null);
  const filterScopeTabs = filters?.catalog?.tabs ?? filters?.tabs;
  const hasPopoverFilters =
    filters !== undefined &&
    (filters.fields.some((field) => isFilterValueSet(filters.values[field.id])) ||
      appliedScopeTabs(filterScopeTabs) !== undefined);
  const hasServerSearchValue =
    isServerSearch && serverSearchDraft.length > 0;
  const hasFilters =
    tableState.columnFilters.length > 0 ||
    hasScopeFilters ||
    hasPopoverFilters ||
    hasServerSearchValue;
  const selectedCount = table.getFilteredSelectedRowModel().rows.length;
  const groupingPath = groupingPathKey(groupBy?.value ?? tableState.grouping);
  const groupByLabel = groupBy?.label ?? "Group by";
  const selectedView = groupBy?.options.find(
    (option) => groupingPathKey(optionGrouping(option)) === groupingPath
  );
  const groupItems =
    groupBy === undefined
      ? []
      : [
          { icon: Layers, label: "No grouping", value: GROUP_NONE },
          ...groupBy.options.map((option) => ({
            icon: option.icon,
            label: option.label,
            value: groupingPathKey(optionGrouping(option)),
          })),
        ];
  const viewTag =
    groupBy?.tag === true && selectedView !== undefined ? (
      <FilterableTableGroupTag
        icon={selectedView.icon}
        label={selectedView.label}
      />
    ) : null;
  const renderedServerGrouping =
    serverGrouping === undefined
      ? undefined
      : {
          ...serverGrouping,
          groups: serverGrouping.groups.map((group) => ({
            ...group,
            label: resolveServerGroupLabel(selectedView, group),
          })),
        };

  function resetFilters(): void {
    resetSearchDraft();
    setTableState((previous) => ({
      ...previous,
      columnFilters: [],
      pagination: { ...previous.pagination, pageIndex: 0 },
    }));
    if (onReset !== undefined) {
      onReset();
      return;
    }
    for (const scope of scopes ?? []) {
      if (scope.value != null) scope.onChange(null);
    }
    if (isServerSearch) search?.onChange?.("");
    if (filters !== undefined) {
      if (filters.onClear !== undefined) {
        filters.onClear();
      } else {
        for (const field of filters.fields) {
          if (isFilterValueSet(filters.values[field.id])) {
            filters.onChange(field.id, undefined);
          }
        }
      }
    }
  }

  function changeScope(scope: FilterableTableScope, next: string | null): void {
    const chosen = next === null || next === GROUP_NONE ? null : String(next);
    scope.onChange(chosen);
  }

  function changeGrouping(next: string | null): void {
    const chosen = next === null ? GROUP_NONE : String(next);
    const nextGrouping =
      chosen === GROUP_NONE ? [] : chosen.split(GROUP_PATH_SEP);
    if (groupBy?.onChange !== undefined) {
      groupBy.onChange(nextGrouping);
      return;
    }
    table.setGrouping(nextGrouping);
  }

  function changeSearch(next: string): void {
    if (isServerSearch) {
      setServerSearchDraft(next);
      return;
    }
    searchColumn?.setFilterValue(next.length > 0 ? next : undefined);
  }

  const groupControl =
    groupingEnabled ? (
      <FilterableTableGroupControl
        items={groupItems}
        label={groupByLabel}
        value={groupingPath}
        onChange={changeGrouping}
      />
    ) : null;

  const toolbar = (
    <Stack
      gap={hasPopoverFilters || viewTag !== null ? "md" : "none"}
      className={fill ? "shrink-0" : undefined}
    >
      <Inline data-slot="table-toolbar" gap="sm" wrap align="center" justify="between">
        <Inline gap="sm" wrap align="center">
          {title === undefined ? null : (
            /* The same heading a PageSection draws, on the control row instead of above it. */
            <Inline gap="sm" align="center" className="min-w-0">
              {title.icon === undefined ? null : (
                <Icon icon={title.icon} size="sm" className="text-ink-subtle" />
              )}
              <Box render={<h2 />} className="truncate text-title font-medium text-ink">
                {title.text}
              </Box>
            </Inline>
          )}
          {tabs === undefined ? null : (
            <Inline gap="xs" align="center">
              {tabs.icon === undefined ? null : (
                <Icon icon={tabs.icon} size="sm" className="text-ink-subtle" />
              )}
              <Box
                render={<span />}
                aria-hidden
                className={LABEL_CLASS}
              >
                {`${tabs.label}:`}
              </Box>
              <Tabs value={tabs.value} onValueChange={tabs.onValueChange}>
                <TabsList variant="ghost" aria-label={tabs.label}>
                  {tabs.items.map((item) => (
                    <TabsTrigger key={item.value} value={item.value}>
                      {item.label}
                    </TabsTrigger>
                  ))}
                </TabsList>
              </Tabs>
            </Inline>
          )}
          {search === undefined ? null : (
            <TableSearchField
              search={search}
              value={searchValue}
              onChange={changeSearch}
            />
          )}
          {(facets ?? []).map((facet) => (
            <DataGridColumnFilter
              key={facet.columnId}
              column={table.getColumn(facet.columnId)}
              title={facet.title}
              options={[...facet.options]}
            />
          ))}
          {(scopes ?? []).map((scope) => (
            <Select
              key={scope.id}
              value={scope.value ?? GROUP_NONE}
              items={[
                { label: scope.title, value: GROUP_NONE },
                ...scope.options.map((option) => ({
                  label: option.label,
                  value: option.value,
                })),
              ]}
              onValueChange={(next) => changeScope(scope, next)}
            >
              <SelectTrigger
                aria-label={scope.title}
                className={SCOPE_TRIGGER_CLASS}
              >
                <SelectValue placeholder={scope.title} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={GROUP_NONE}>{scope.title}</SelectItem>
                {scope.options.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ))}
          {hasFilters ? (
            <Button variant="ghost" onClick={resetFilters}>
              <Icon icon={X} size="sm" />
              Reset
            </Button>
          ) : null}
        </Inline>
        <Inline gap={gallery === undefined ? "md" : "sm"} wrap={gallery !== undefined} align="center">
          {selectable ? (
            <Box
              render={<span />}
              className="font-mono text-meta text-ink-subtle"
            >
              {`${selectedCount} of ${recordCount} selected`}
            </Box>
          ) : null}
          {filters === undefined ? null : (
            <TableFilters
              fields={filters.fields}
              values={filters.values}
              onChange={filters.onChange}
              onClear={filters.onClear}
              tabs={filters.tabs}
              catalog={filters.catalog}
              catalogs={filters.catalogs}
            />
          )}
          {groupControl}
          {galleryActive || !columnVisibilityControl ? null : <DataGridColumnVisibility
            table={table}
            trigger={
              <Button variant="outline">
                <Icon icon={Settings2} size="sm" />
                Columns
              </Button>
            }
          />}
          {gallery === undefined ? null : (
            <ToggleGroup
              aria-label="Catalog view"
              value={[gallery.view]}
              onValueChange={(values) => {
                const next = values[0];
                if (next === "table" || next === "gallery") gallery.onViewChange(next);
              }}
            >
              <ToggleGroupItem value="table" aria-label="Table view">
                <Icon icon={Table2} size="sm" />
                Table
              </ToggleGroupItem>
              <ToggleGroupItem value="gallery" aria-label="Gallery view">
                <Icon icon={LayoutGrid} size="sm" />
                Gallery
              </ToggleGroupItem>
            </ToggleGroup>
          )}
          {actions}
        </Inline>
      </Inline>
      {viewTag === null && (filters === undefined || !hasPopoverFilters) ? null : (
        <Inline gap="xs" wrap align="center">
          {viewTag}
          {filters === undefined || !hasPopoverFilters ? null : (
            <TableFilterPills
              fields={filters.fields}
              values={filters.values}
              onChange={filters.onChange}
              tabs={filterScopeTabs}
              catalog={filters.catalog}
              catalogs={filters.catalogs}
            />
          )}
        </Inline>
      )}
    </Stack>
  );

  // Expand-as-rows renders children the virtualizer cannot see, so a table that discloses is never windowed.
  const virtualized =
    fill && getRowCanExpand === undefined && table.getRowModel().rows.length > VIRTUALIZE_ABOVE_ROWS;
  function renderGalleryItems(busy: boolean): ReactNode {
    if (!busy && table.getRowModel().rows.length === 0) {
      return <Box className="py-6 text-center text-body text-ink-subtle">{emptyMessage}</Box>;
    }
    return (
      <Box className="grid grid-cols-1 gap-6 @xl:grid-cols-2 @4xl:grid-cols-3 @7xl:grid-cols-4">
        {busy
          ? Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-80 rounded-panel" />)
          : table.getRowModel().rows.map((row) => (
              <Box key={row.id} className="min-w-0">{gallery?.renderItem(row.original)}</Box>
            ))}
      </Box>
    );
  }

  const grid = galleryActive ? (
    <ScrollArea overflow="vertical" className={fill ? "min-h-0 flex-1" : undefined}>
    <Stack
      gap="lg"
      data-slot="table-gallery"
      aria-busy={loading || undefined}
      aria-label={loading ? loadingLabel : "Gallery"}
      className="@container px-0.5 py-2"
    >
      {loading || renderedServerGrouping === undefined ? renderGalleryItems(loading) : (
        renderedServerGrouping.groups.length === 0 ? renderGalleryItems(false) :
        renderedServerGrouping.groups.map((group) => (
          <Stack key={group.id} gap="md">
            <FilterableTableGroupRow
              count={group.count}
              expanded={group.expanded}
              label={group.label}
              onToggle={group.onToggle}
            />
            {group.expanded ? renderGalleryItems(group.isLoading) : null}
            {group.expanded && !group.isLoading && group.hasMore ? (
              <Inline justify="center">
                <Button variant="outline" size="compact" loading={group.isFetchingMore} onClick={group.onFetchMore}>
                  Load more in {group.labelText}
                </Button>
              </Inline>
            ) : null}
          </Stack>
        ))
      )}
      {renderedServerGrouping?.hasMore ? (
        <Inline justify="center">
          <Button variant="outline" size="compact" loading={renderedServerGrouping.isFetchingMore} onClick={renderedServerGrouping.onFetchMore}>
            Load more groups
          </Button>
        </Inline>
      ) : null}
      {infinite === undefined || loading ? null : (
        <>
          <DataGridLoadMoreSentinel
            enabled={infinite.hasMore && !infinite.isFetchingMore && infinite.pause !== true}
            onVisible={infinite.onFetchMore}
          />
          {infinite.isFetchingMore ? <Skeleton aria-label="Loading more rows" className="h-control w-full" /> : null}
        </>
      )}
    </Stack>
    </ScrollArea>
  ) : (
    <Box
      radius="panel"
      border="line"
      bg="panel"
      aria-busy={loading || undefined}
      aria-label={loading ? loadingLabel : undefined}
      className={fill ? "min-h-0 flex-1 overflow-hidden" : "overflow-hidden"}
    >
      <DataGridContainer className={fill ? "h-full" : undefined}>
        {renderedServerGrouping === undefined ? (
          virtualized ? (
            <DataGridTableVirtual height="100%" estimateSize={ROW_HEIGHT[density]} />
          ) : (
            <DataGridTable />
          )
        ) : (
          <DataGridTableServerGrouped grouping={renderedServerGrouping} />
        )}
        {infinite === undefined || loading ? null : (
          <>
            <DataGridLoadMoreSentinel
              enabled={
                infinite.hasMore &&
                !infinite.isFetchingMore &&
                infinite.pause !== true
              }
              onVisible={infinite.onFetchMore}
            />
            {infinite.isFetchingMore ? (
              <Box
                aria-label="Loading more rows"
                aria-busy
                className="h-row-data w-full"
              >
                <Skeleton className={LOAD_MORE_SKELETON_CLASS} />
              </Box>
            ) : null}
          </>
        )}
      </DataGridContainer>
    </Box>
  );

  /* A cursor page does not know the total, so the readout counts from where this page starts and a
     trailing + says more follow. An empty first page has nothing to count or page, so no footer. */
  const cursorPageEmpty =
    cursorPagination !== undefined && data.length === 0 && !cursorPagination.hasPrevious;
  const pagination =
    infinite !== undefined || serverGrouping !== undefined || loading || cursorPageEmpty ? null : (
      <DataGridPagination
        {...(cursorPagination === undefined
          ? {}
          : {
              showSizes: false,
              showPageNumbers: false,
              canPreviousPage: cursorPagination.hasPrevious,
              canNextPage: cursorPagination.hasNext,
              onPreviousPage: cursorPagination.onPrevious,
              onNextPage: cursorPagination.onNext,
              info: cursorReadout(cursorPagination, data.length),
            })}
      />
    );

  return (
    <DataGrid
      table={table}
      recordCount={recordCount}
      emptyMessage={emptyMessage}
      isLoading={loading}
      loadingMessage={loadingLabel}
      selectedRowId={selectedRowId}
      onRowClick={handleRowClick}
      isRowClickable={isRowClickable ?? getRowCanExpand}
      isRowFresh={isRowFresh}
      tableLayout={{
        columnsMovable: true,
        columnsPinnable: true,
        columnsResizable: true,
        columnsVisibility: true,
        headerBackground: true,
        headerSticky: fill,
        width: "fixed",
      }}
    >
      {frame === undefined ? (
        <Stack gap="md" grow={fill} className={fill ? "h-full min-h-0" : undefined}>
          {toolbar}
          {grid}
          {pagination}
        </Stack>
      ) : (
        frame({ grid, pagination, toolbar })
      )}
    </DataGrid>
  );
}

export { ExpandedRow } from "../data-grid/data-grid-expanded-rows";
export {
  FILTERABLE_TABLE_RULES,
  FilterableTable,
  FilterableTableGroupControl,
  FilterableTableGroupRow,
  FilterableTableGroupTag,
  filterableTableSplitFrame,
};
export type {
  FilterableTableCursorPagination,
  FilterableTableFacet,
  FilterableTableFilters,
  FilterableTableFrame,
  FilterableTableGroupBy,
  FilterableTableGroupControlItem,
  FilterableTableGroupOption,
  FilterableTableGroupRowProps,
  FilterableTableGroupTagProps,
  FilterableTableInfinite,
  FilterableTableProps,
  FilterableTableScope,
  FilterableTableScopeOption,
  FilterableTableServerGrouping,
  FilterableTableSearch,
  FilterableTableSearchResult,
  FilterableTableSearchResults,
  FilterableTableState,
  FilterableTableTab,
  FilterableTableTabs,
  FilterableTableTitle,
};
