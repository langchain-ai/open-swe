"use client";
"use no memo";

/*
 * ReUI `data-grid` core, vendored (free tier, no licence key) and retokenized.
 *
 * Source: https://reui.io/r/data-grid.json, 2026-08-04. Provenance and the list
 * of pieces deliberately not vendored live in web/reference/VENDORED.md.
 *
 * The public API is ReUI's: DataGrid, DataGridContainer, DataGridProvider,
 * useDataGrid, getColumnHeaderLabel. What changed is the styling vocabulary
 * (shadcn names -> CORE 14 tokens) and the two places where React's compiler
 * lint rules reject ReUI's original code; both are called out where they occur.
 */

import { createContext, useContext, useMemo } from "react";
import type { ReactNode } from "react";
import type {
  Column,
  ColumnFiltersState,
  RowData,
  SortingState,
  Table,
} from "@tanstack/react-table";

import { cn } from "../ui/cn";
import { ScrollArea } from "../ui/scroll-area";

declare module "@tanstack/react-table" {
  interface ColumnMeta<TData extends RowData, TValue> {
    headerTitle?: string;
    headerClassName?: string;
    cellClassName?: string;
    skeleton?: ReactNode;
    expandedContent?: (row: TData) => ReactNode;
    /**
     * Sibling body rows keyed by parent column id. Prefer this when the
     * nested grain shares the parent measures. `expandedContent` stays the
     * colspan panel. Expand-as-rows is not virtualized.
     */
    expandedRows?: (row: TData) => ReactNode;
    autoSize?: boolean;
    /**
     * Seeds FilterableTable pinning. The header control can still unpin.
     * Product routes set this; they do not call DataGrid pin APIs.
     */
    pin?: "left" | "right";
    /**
     * Phantom. TanStack declares `ColumnMeta` with a value type parameter and
     * TypeScript requires a merged declaration to repeat it identically, so the
     * augmentation has to reference `TValue` somewhere. Nothing reads this.
     */
    readonly valueType?: TValue;
  }
}

/** Label for headers / column visibility: `meta.headerTitle`, string `columnDef.header`, or `column.id`. */
export function getColumnHeaderLabel<TData, TValue>(
  column: Column<TData, TValue>
): string {
  const meta = column.columnDef.meta as { headerTitle?: string } | undefined;
  if (typeof meta?.headerTitle === "string") return meta.headerTitle;
  const defHeader = column.columnDef.header;
  if (typeof defHeader === "string") return defHeader;
  return String(column.id);
}

export type DataGridApiFetchParams = {
  pageIndex: number;
  pageSize: number;
  sorting?: SortingState;
  filters?: ColumnFiltersState;
  searchQuery?: string;
};

export type DataGridApiResponse<T> = {
  data: T[];
  empty: boolean;
  pagination: {
    total: number;
    page: number;
  };
};

export type DataGridAutoSizeController = {
  /**
   * Grows the first visible `meta.autoSize` column by the given free space.
   * Applies at most once per column id; safe to call from every viewport
   * measurement. Returns true when a sizing update was dispatched.
   */
  apply: (fillWidth: number) => boolean;
};

export type DataGridRequestParams = {
  pageIndex: number;
  pageSize: number;
  sorting?: SortingState;
  columnFilters?: ColumnFiltersState;
};

export interface DataGridProps<TData> {
  className?: string;
  table?: Table<TData>;
  recordCount: number;
  children?: ReactNode;
  onRowClick?: (row: TData) => void;
  /** Narrows row click affordance when only some leaf kinds are interactive. */
  isRowClickable?: (row: TData) => boolean;
  /** Tints the rows a live list just received or changed, for one paint. */
  isRowFresh?: (row: TData) => boolean;
  /** Marks the leaf whose inspector is open. Group rows never match. */
  selectedRowId?: string;
  isLoading?: boolean;
  loadingMode?: "skeleton" | "spinner";
  loadingMessage?: ReactNode | string;
  fetchingMoreMessage?: ReactNode | string;
  allRowsLoadedMessage?: ReactNode | string;
  emptyMessage?: ReactNode | string;
  tableLayout?: {
    dense?: boolean;
    cellBorder?: boolean;
    rowBorder?: boolean;
    rowRounded?: boolean;
    stripped?: boolean;
    headerBackground?: boolean;
    footerBackground?: boolean;
    headerBorder?: boolean;
    headerSticky?: boolean;
    width?: "auto" | "fixed";
    columnsVisibility?: boolean;
    columnsResizable?: boolean;
    columnsResizeMode?: "onChange" | "onEnd";
    columnsPinnable?: boolean;
    columnsMovable?: boolean;
    columnsDraggable?: boolean;
    rowsDraggable?: boolean;
    rowsPinnable?: boolean;
  };
  tableClassNames?: {
    base?: string;
    header?: string;
    headerRow?: string;
    headerSticky?: string;
    body?: string;
    bodyRow?: string;
    footer?: string;
    edgeCell?: string;
  };
}

export interface DataGridContextProps<TData> {
  props: DataGridProps<TData>;
  table: Table<TData>;
  recordCount: number;
  isLoading: boolean;
  /**
   * Internal coordinator for `meta.autoSize` columns. Lives at the core level
   * so every table variant and viewport instance shares one application state.
   */
  autoSize?: DataGridAutoSizeController;
}

function createDataGridAutoSizeController<TData>(
  table: Table<TData>
): DataGridAutoSizeController {
  let applied: { columnId: string; base: number; grown: number } | null = null;

  return {
    apply(fillWidth: number) {
      const columnSizing = table.getState().columnSizing;

      // Re-arm after reset flows (double-click resetSize, resetColumnSizing,
      // controlled state replacement) so the column re-fills instead of
      // leaving a dead blank strip.
      if (applied && columnSizing[applied.columnId] === undefined) {
        applied = null;
      }

      if (fillWidth <= 0) return false;

      const autoSizeColumn = table
        .getVisibleLeafColumns()
        .find(
          (column) => column.columnDef.meta?.autoSize && column.getCanResize()
        );

      if (!autoSizeColumn || applied?.columnId === autoSizeColumn.id) {
        return false;
      }

      // Candidate switched (e.g. the grown column was hidden and another
      // meta.autoSize column took over): revert the previous growth if the
      // user hasn't manually resized that column since, so visibility
      // toggles cannot ratchet the table wider than its container forever.
      const revert =
        applied && columnSizing[applied.columnId] === applied.grown
          ? applied
          : null;
      const base = columnSizing[autoSizeColumn.id] ?? autoSizeColumn.getSize();
      const grown = base + fillWidth;

      applied = { columnId: autoSizeColumn.id, base, grown };
      table.setColumnSizing((old) => {
        const next = { ...old, [autoSizeColumn.id]: grown };
        if (revert && next[revert.columnId] === revert.grown) {
          next[revert.columnId] = revert.base;
        }
        return next;
      });

      return true;
    },
  };
}

/**
 * The default row shape a consumer gets when it does not name one.
 *
 * One context object has to serve grids of every row shape, and TanStack's
 * `Table<TData>` is invariant in `TData` (its accessor functions take a row in
 * parameter position), so no widening type can describe all of them. ReUI
 * erases with `any`; this suite erases at exactly one boundary instead -- the
 * context is stored untyped and `useDataGrid<TData>()` asserts it back -- so
 * the escape hatch is one greppable line rather than an `any` in every file.
 */
type DataGridErasedRow = Record<string, unknown>;

const DataGridContext = createContext<unknown>(undefined);

function useDataGrid<TData = DataGridErasedRow>(): DataGridContextProps<TData> {
  const context = useContext(DataGridContext) as
    | DataGridContextProps<TData>
    | undefined;
  if (!context) {
    throw new Error("useDataGrid must be used within a DataGridProvider");
  }
  return context;
}

function DataGridProvider<TData extends object>({
  children,
  table,
  ...props
}: DataGridProps<TData> & { table: Table<TData> }) {
  /*
   * DEVIATION from ReUI: the upstream provider keeps a latest-props ref that it
   * writes during render, and memoizes the context value on a hand-picked slice
   * of table state. `react-hooks` v6 carries the React Compiler correctness
   * rules and rejects both (`react-hooks/refs`: refs may not be written during
   * render; `react-hooks/use-memo`: dependencies must be simple expressions, so
   * `JSON.stringify(props.tableLayout)` is out). Silencing them would spend the
   * escape-hatch ratchet on a micro-optimisation, so the value is published
   * plain: correctness first, and wiki 01 principle 8 asks for exactly this
   * ("manual memoization only at profiler-proven hot spots"). The expensive
   * path is unaffected -- the body-rows memo in data-grid-table.tsx still
   * blocks re-renders while a column resize drag is live.
   */
  const autoSize = useMemo(
    () => createDataGridAutoSizeController(table),
    [table]
  );

  const value: DataGridContextProps<TData> = {
    props,
    table,
    recordCount: props.recordCount,
    isLoading: props.isLoading || false,
    autoSize,
  };

  return (
    <DataGridContext.Provider value={value}>
      {children}
    </DataGridContext.Provider>
  );
}

function DataGrid<TData extends object>({
  children,
  table,
  ...props
}: DataGridProps<TData>) {
  const defaultProps: Partial<DataGridProps<TData>> = {
    loadingMode: "skeleton",
    tableLayout: {
      dense: false,
      cellBorder: false,
      rowBorder: true,
      rowRounded: false,
      stripped: false,
      headerSticky: false,
      headerBackground: false,
      footerBackground: false,
      headerBorder: true,
      width: "fixed",
      columnsVisibility: false,
      columnsResizable: false,
      // columnsResizeMode has no default on purpose: when unset, the
      // consumer's tanstack columnResizeMode (default "onEnd") is honored.
      columnsPinnable: false,
      columnsMovable: false,
      columnsDraggable: false,
      rowsDraggable: false,
      rowsPinnable: false,
    },
    tableClassNames: {
      base: "",
      header: "",
      headerRow: "",
      // z-40 keeps the sticky header above pinned body cells (zIndex 30 in
      // getPinningStyles), which would otherwise paint over it while
      // scrolling vertically with columnsPinnable enabled.
      headerSticky: "sticky top-0 z-40 bg-panel",
      body: "",
      bodyRow: "",
      footer: "",
      edgeCell: "",
    },
  };

  const mergedProps: DataGridProps<TData> = {
    ...defaultProps,
    ...props,
    tableLayout: {
      ...defaultProps.tableLayout,
      ...(props.tableLayout || {}),
    },
    tableClassNames: {
      ...defaultProps.tableClassNames,
      ...(props.tableClassNames || {}),
    },
  };

  // Ensure table is provided
  if (!table) {
    throw new Error('DataGrid requires a "table" prop');
  }

  /*
   * DEVIATION from ReUI: upstream re-asserts `table.options.columnResizeMode`
   * by assigning to it during render, which `react-hooks/immutability` rejects
   * (a prop object may not be mutated). `setOptions` is TanStack's own public
   * path for the same thing -- it is what the React adapter calls on every
   * render -- so the behaviour (an explicit tableLayout resize mode wins over
   * the consumer's useReactTable option) is preserved.
   */
  const resizeMode = mergedProps.tableLayout?.columnsResizable
    ? mergedProps.tableLayout.columnsResizeMode
    : undefined;
  if (resizeMode && table.options.columnResizeMode !== resizeMode) {
    table.setOptions((previous) => ({
      ...previous,
      columnResizeMode: resizeMode,
    }));
  }

  return (
    <DataGridProvider table={table} {...mergedProps}>
      {children}
    </DataGridProvider>
  );
}

/** `hover` is the overlay thumb; `always` is the native bar grids may keep. */
export type DataGridScrollbars = "hover" | "always";

/*
 * The grid's scroll box, and the second half of the table exception to the
 * scrollbar law (wiki 02): scroll surfaces take the overlay thumb, and only
 * tables and grids may opt into a persistent bar, because on data wide enough
 * to need sideways scrolling the bar is the affordance that says so.
 *
 * `hover` puts a ScrollArea inside the container rather than around it, which
 * matters: `getDataGridScrollAreaViewport` in data-grid-table.tsx binds the
 * width measurement and the virtualizer to the nearest scroll viewport that is
 * inside `[data-slot="data-grid"]`, and refuses a page-level one. Keeping the
 * ScrollArea within the container is what makes it the grid's own viewport
 * instead of an ancestor the grid must ignore.
 */
function DataGridContainer({
  children,
  className,
  scrollbars = "hover",
}: {
  children: ReactNode;
  className?: string;
  scrollbars?: DataGridScrollbars;
  /** Accepted for backwards compatibility; currently has no effect. */
  border?: boolean;
}) {
  if (scrollbars === "always") {
    return (
      <div
        data-slot="data-grid"
        data-scrollbars={scrollbars}
        className={cn("w-full overflow-auto", className)}
      >
        {children}
      </div>
    );
  }
  return (
    <div
      data-slot="data-grid"
      data-scrollbars={scrollbars}
      className={cn("w-full overflow-hidden", className)}
    >
      <ScrollArea recoveryKey="grid" className="size-full">{children}</ScrollArea>
    </div>
  );
}

export { useDataGrid, DataGridProvider, DataGrid, DataGridContainer };
export type { DataGridErasedRow };
