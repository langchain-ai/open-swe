"use client";

/*
 * The one table wrapper (wiki 01 principle 13: "grids are wrapped, because
 * Table v9 is coming").
 *
 * Every grid in the app calls `useAppTable`. Nothing else calls
 * `useReactTable`. TanStack Table v9 (TanStack Store, tree-shakable) is in beta
 * and not production material for an analytics surface, so when it lands the
 * migration edits this file and the grids stay put.
 *
 * It carries three things beyond the raw hook:
 *   1. the defaults every grid in this product wants (row-model wiring,
 *      pagination sizes, the manual-vs-client switch),
 *   2. a required `getRowId`, because rows are addressed by their server id in
 *      selection state, SSE invalidation, and URL state -- array indexes go
 *      stale the moment a row moves,
 *   3. the row-height contract the design system expresses in tokens, so a
 *      caller that needs a pixel number for virtualization does not invent one.
 */

import { useMemo } from "react";
import {
  getCoreRowModel,
  getExpandedRowModel,
  getFacetedRowModel,
  getFacetedUniqueValues,
  getFilteredRowModel,
  getGroupedRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from "../lib/table__tanstack";
import type {
  ColumnDef,
  RowSelectionState,
  Table,
  TableOptions,
  TableState,
  Updater,
} from "@tanstack/react-table";

import { withHeaderSafeColumnSizing } from "../lib/table__header-safe-column-size";

/**
 * The density ladder, in pixels, for the callers that need a number.
 *
 * These mirror `--spacing-row-*` in `src/app/globals.css` exactly. CSS stays the
 * source of truth for what a row *looks* like: a grid styles itself with
 * `h-row-data` and only reads these when handing an estimate to the virtualizer,
 * which cannot resolve a class.
 */
export const ROW_HEIGHT = {
  /** `h-row-data` -- scanning a table of records. The default. */
  data: 40,
  /** `h-row-record` -- a row that is itself an object you act on. */
  record: 44,
  /** `h-row-convo` -- message and thread rows. */
  convo: 48,
} as const;

export type RowDensity = keyof typeof ROW_HEIGHT;

/** Page sizes offered by `DataGridPagination`; 25 is the default first page. */
export const PAGE_SIZES = [10, 25, 50, 100] as const;

export const DEFAULT_PAGE_SIZE = 25;

/**
 * Past this many mounted rows a grid must virtualize (wiki 01 principle 12).
 * Server pagination first, `DataGridTableVirtual` second, never an unbounded
 * list.
 */
export const VIRTUALIZE_ABOVE_ROWS = 200;

interface UseAppTableOptions<TData> {
  data: TData[];
  columns: ColumnDef<TData, unknown>[];
  /**
   * Stable identity for a row, required. Prefer the server's id; the value ends
   * up in row-selection state, SSE invalidation, and the URL.
   */
  getRowId: (row: TData, index: number) => string;
  /** Row height this grid renders at; drives the virtualizer's estimate. */
  density?: RowDensity;
  /**
   * Total row count when the server paginates. Supplying it turns on manual
   * pagination and is what `DataGrid`'s `recordCount` should be fed.
   */
  rowCount?: number;
  /** Sorting/filtering/pagination happen on the server. Defaults to `rowCount !== undefined`. */
  manual?: boolean;
  /** Controlled slices of table state, merged over the defaults. */
  state?: Partial<TableState>;
  onRowSelectionChange?: (updater: Updater<RowSelectionState>) => void;
  /** Escape hatch to the raw TanStack options; merged last, so it wins. */
  tableOptions?: Partial<TableOptions<TData>>;
}

interface AppTable<TData> {
  table: Table<TData>;
  /** `recordCount` for `<DataGrid>`: the server total when known, else the client row count. */
  recordCount: number;
  /** Pixel estimate for `DataGridTableVirtual`'s `estimateSize`. */
  estimatedRowHeight: number;
  /** True once the mounted row count crosses the virtualization threshold. */
  shouldVirtualize: boolean;
}

/**
 * Build the app's table instance. The only sanctioned entry point to TanStack
 * Table.
 */
export function useAppTable<TData>({
  data,
  columns,
  getRowId,
  density = "data",
  rowCount,
  manual,
  state,
  onRowSelectionChange,
  tableOptions,
}: UseAppTableOptions<TData>): AppTable<TData> {
  const isManual = manual ?? rowCount !== undefined;
  const sizedColumns = useMemo(
    () => withHeaderSafeColumnSizing(columns),
    [columns]
  );

  const {
    initialState: callerInitialState,
    state: callerState,
    ...restTableOptions
  } = tableOptions ?? {};

  const table = useReactTable<TData>({
    data,
    columns: sizedColumns,
    getRowId,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getGroupedRowModel: getGroupedRowModel(),
    /*
     * The client-side sort/filter/paginate models are inert under manual mode --
     * TanStack skips them when the matching `manual*` flag is set -- so wiring
     * them unconditionally keeps one code path for both server-paged grids and
     * the small client-side ones. Grouping is the same: an empty grouping
     * state leaves the model inert, so a surface that never groups still
     * shares this wrapper.
     */
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getFacetedRowModel: getFacetedRowModel(),
    getFacetedUniqueValues: getFacetedUniqueValues(),
    manualPagination: isManual,
    manualSorting: isManual,
    manualFiltering: isManual,
    rowCount,
    /* Committed on release: a resize drag must not re-render every row per frame. */
    columnResizeMode: "onEnd",
    onRowSelectionChange,
    ...restTableOptions,
    initialState: {
      pagination: { pageIndex: 0, pageSize: DEFAULT_PAGE_SIZE },
      ...callerInitialState,
    },
    state: {
      ...callerState,
      ...state,
    },
  });

  const mountedRowCount = table.getRowModel().rows.length;

  return {
    table,
    recordCount: rowCount ?? data.length,
    estimatedRowHeight: ROW_HEIGHT[density],
    shouldVirtualize: mountedRowCount > VIRTUALIZE_ABOVE_ROWS,
  };
}

export type { AppTable, UseAppTableOptions };
