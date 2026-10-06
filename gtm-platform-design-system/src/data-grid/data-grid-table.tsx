"use client";
"use no memo";

/*
 * ReUI `data-grid` table, vendored (free tier, no licence key) and retokenized.
 *
 * Source: https://reui.io/r/data-grid.json, 2026-08-04. Provenance and cut
 * pieces: web/reference/VENDORED.md.
 *
 * Retokenization map applied throughout this suite (shadcn -> CORE 14):
 *   text-foreground            -> text-ink
 *   text-muted-foreground      -> text-ink-subtle
 *   text-secondary-foreground  -> text-ink-muted
 *   text-sm / text-xs          -> text-body / text-meta
 *   bg-card / bg-background    -> bg-panel
 *   hover:bg-muted/40          -> hover:bg-hover
 *   selected / pinned rows     -> bg-selected
 *   border colours             -> border-line (hairline) / border-line-strong
 *   rounded-lg / -md / -sm     -> rounded-panel / -control / -compact
 *   h-10 header cell           -> h-row-data (40); dense -> h-control (32)
 *   dark: variants             -> deleted; the tokens remap on [data-theme]
 *   inset box-shadow edges     -> real hairline borders (shadow-[...] is an
 *                                 arbitrary value, banned by the design lint)
 */

import {
  Fragment,
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
} from "react";
import type {
  CSSProperties,
  MouseEvent as ReactMouseEvent,
  ReactNode,
  TouchEvent as ReactTouchEvent,
  Ref,
  RefObject,
} from "react";
import { flexRender } from "../lib/table__tanstack";
import type { Cell, Column, Header, Row, Table } from "@tanstack/react-table";

import { Box, Inline } from "../ui/box";
import { cn } from "../ui/cn";
import { Checkbox } from "../ui/checkbox";
import { GroupRowContent } from "../ui/group-row";
import { Skeleton } from "../ui/skeleton";
import { Spinner } from "../ui/spinner";
import {
  ChevronDownGlyph,
  PinFilledGlyph,
  PinGlyph,
} from "./data-grid-glyphs";
import { DataGridLoadMoreSentinel } from "./data-grid-load-more";
import { useDataGrid } from "./data-grid";

// Static spacing lookups; called once per cell, so they stay plain string
// picks instead of runtime variant machinery.
/* A filter change must not paint 100 empty rows, or a first page of 1. */
const SKELETON_ROW_MIN = 8;
const SKELETON_ROW_MAX = 12;
const SKELETON_CELL_WIDTHS = ["w-3/4", "w-1/2", "w-2/3", "w-5/12"] as const;

function skeletonRowCount(pageSize: number | undefined): number {
  if (pageSize === undefined || !Number.isFinite(pageSize) || pageSize < 1) {
    return SKELETON_ROW_MIN;
  }
  return Math.min(Math.max(pageSize, SKELETON_ROW_MIN), SKELETON_ROW_MAX);
}

function DefaultSkeletonCell({ columnIndex }: { columnIndex: number }) {
  return (
    <Skeleton
      className={cn(
        "h-3",
        SKELETON_CELL_WIDTHS[columnIndex % SKELETON_CELL_WIDTHS.length]
      )}
    />
  );
}

const headerCellSpacingVariants = ({ size }: { size?: "dense" | "default" }) =>
  size === "dense" ? "px-2.5 h-control" : "px-4";

const bodyCellSpacingVariants = ({ size }: { size?: "dense" | "default" }) =>
  size === "dense" ? "px-2 py-1.5" : "px-3 py-2";

const footerCellSpacingVariants = ({ size }: { size?: "dense" | "default" }) =>
  size === "dense" ? "px-2 py-1.5" : "px-3 py-2";

/*
 * Pinned-column edges. Upstream draws them with an inset box-shadow so the
 * sticky cell keeps its width; a hairline border does the same here because
 * every cell is border-box, and it keeps the colour on a token.
 */
const PINNED_EDGE_CLASS =
  "data-[last-col=left]:border-e data-[last-col=left]:border-line-strong data-[last-col=right]:border-s data-[last-col=right]:border-line-strong";

/* The 2px rule that separates a pinned row block from the scrolling rows. */
const PINNED_ROW_BOUNDARY_CLASS = "[&>td]:border-b-2 [&>td]:border-line-strong";

function getPinningStyles<TData>(column: Column<TData>): CSSProperties {
  const isPinned = column.getIsPinned();

  return {
    // Logical offsets: TanStack's "left"/"right" buckets are start/end
    // semantics, so pinned columns stick to the correct edge in RTL too
    // (identical to left/right in LTR).
    insetInlineStart:
      isPinned === "left" ? `${column.getStart("left")}px` : undefined,
    insetInlineEnd:
      isPinned === "right" ? `${column.getAfter("right")}px` : undefined,
    position: isPinned ? "sticky" : undefined,
    transform: isPinned ? "translateZ(0)" : undefined,
    contain: isPinned ? "paint" : undefined,
    width: column.getSize(),
    zIndex: isPinned ? 30 : undefined,
    backgroundClip: isPinned ? "padding-box" : undefined,
  };
}

// Shared indent contract for tree rows: DataGridTableRowExpand consumes it,
// and fully custom cells can reuse it for depth alignment without the
// built-in toggle.
function getDataGridTreeIndentStyle<TData>(
  row: Row<TData>,
  indent: number = 20
): CSSProperties {
  return {
    "--data-grid-tree-padding": `${row.depth * indent}px`,
  } as CSSProperties;
}

function assignRef<T>(ref: Ref<T> | undefined, value: T | null) {
  if (!ref) return;

  if (typeof ref === "function") {
    ref(value);
    return;
  }

  (ref as { current: T | null }).current = value;
}

/**
 * Nearest scroll-area viewport that belongs to THIS grid. A viewport outside
 * the grid's own container (e.g. a page-level ScrollArea) would make the
 * width measurement - and the virtualizer - bind the wrong box.
 */
function getDataGridScrollAreaViewport(node: HTMLElement): HTMLElement | null {
  const scrollViewport = node.closest<HTMLElement>(
    '[data-slot="scroll-area-viewport"]'
  );

  if (!scrollViewport) return null;

  const gridContainer = node.closest('[data-slot="data-grid"]');
  if (gridContainer && !gridContainer.contains(scrollViewport)) return null;

  return scrollViewport;
}

type DataGridResizeStartEvent =
  | ReactMouseEvent<HTMLDivElement>
  | ReactTouchEvent<HTMLDivElement>;

type DataGridResizeDocumentEvent =
  | globalThis.MouseEvent
  | globalThis.TouchEvent;

function isDataGridTouchEvent(
  event: DataGridResizeStartEvent | DataGridResizeDocumentEvent
): event is ReactTouchEvent<HTMLDivElement> | globalThis.TouchEvent {
  return "touches" in event;
}

type DataGridTouchListLike = {
  length: number;
  item: (index: number) => { identifier: number; clientX: number } | null;
};

function findTouchClientX(list: DataGridTouchListLike, identifier: number) {
  for (let i = 0; i < list.length; i++) {
    const touch = list.item(i);
    if (touch && touch.identifier === identifier) return touch.clientX;
  }

  return undefined;
}

function getDataGridResizeEventClientX(
  event: DataGridResizeStartEvent | DataGridResizeDocumentEvent,
  touchIdentifier?: number
) {
  if (isDataGridTouchEvent(event)) {
    if (typeof touchIdentifier === "number") {
      return (
        findTouchClientX(event.touches, touchIdentifier) ??
        findTouchClientX(event.changedTouches, touchIdentifier)
      );
    }

    return event.touches[0]?.clientX ?? event.changedTouches[0]?.clientX;
  }

  return event.clientX;
}

function startDataGridColumnResizeOnEnd<TData>(
  event: DataGridResizeStartEvent,
  header: Header<TData, unknown>,
  table: Table<TData>
): (() => void) | undefined {
  const column = table.getColumn(header.column.id);

  if (!column || !column.getCanResize()) return;
  const isTouchSession = isDataGridTouchEvent(event);
  if (isTouchSession && event.touches.length > 1) return;

  event.persist?.();

  const ownerDocument = event.currentTarget.ownerDocument;
  const ownerWindow = ownerDocument.defaultView;
  const previousBodyCursor = ownerDocument.body.style.cursor;
  const previousDocumentCursor = ownerDocument.documentElement.style.cursor;
  const startSize = header.getSize();
  // Track the initiating finger so a second touch cannot move or commit the
  // resize with the wrong clientX.
  const touchIdentifier = isTouchSession
    ? event.touches[0]?.identifier
    : undefined;
  const dragStartClientX = getDataGridResizeEventClientX(
    event,
    touchIdentifier
  );
  const headerCell = event.currentTarget.closest("th");
  const headerRect = headerCell?.getBoundingClientRect();
  const startOffset =
    headerRect &&
    Number.isFinite(
      table.options.columnResizeDirection === "rtl"
        ? headerRect.left
        : headerRect.right
    )
      ? table.options.columnResizeDirection === "rtl"
        ? headerRect.left
        : headerRect.right
      : dragStartClientX;

  if (typeof dragStartClientX !== "number" || typeof startOffset !== "number") {
    return;
  }

  ownerDocument.body.style.cursor = "col-resize";
  ownerDocument.documentElement.style.cursor = "col-resize";

  const columnSizingStart = header
    .getLeafHeaders()
    .map(
      (leafHeader) =>
        [leafHeader.column.id, leafHeader.column.getSize()] as [string, number]
    );
  const directionMultiplier =
    table.options.columnResizeDirection === "rtl" ? -1 : 1;

  // Clamp the drag to the leaf columns' min/max sizes so the preview
  // indicator matches what the commit will produce (no overshoot followed by
  // a snap-back on release). columnDef always carries resolved defaults.
  let minDeltaPercentage = -0.999999;
  let maxDeltaPercentage = Number.POSITIVE_INFINITY;
  columnSizingStart.forEach(([columnId, headerSize]) => {
    if (headerSize <= 0) return;

    const leafColumn = table.getColumn(columnId);
    const minSize = leafColumn?.columnDef.minSize;
    const maxSize = leafColumn?.columnDef.maxSize;

    if (typeof minSize === "number") {
      minDeltaPercentage = Math.max(
        minDeltaPercentage,
        minSize / headerSize - 1
      );
    }
    if (typeof maxSize === "number" && Number.isFinite(maxSize)) {
      maxDeltaPercentage = Math.min(
        maxDeltaPercentage,
        maxSize / headerSize - 1
      );
    }
  });

  let lastClientX = dragStartClientX;
  let ended = false;
  const stopListeners: Array<() => void> = [];

  const updateOffset = (clientXPos?: number, commit = false) => {
    if (typeof clientXPos !== "number") return;

    lastClientX = clientXPos;

    const nextColumnSizing: Record<string, number> = {};
    const deltaPercentage = Math.min(
      Math.max(
        ((clientXPos - dragStartClientX) * directionMultiplier) / startSize,
        minDeltaPercentage
      ),
      maxDeltaPercentage
    );
    const deltaOffset = deltaPercentage * startSize;

    columnSizingStart.forEach(([columnId, headerSize]) => {
      nextColumnSizing[columnId] =
        Math.round(
          Math.max(headerSize + headerSize * deltaPercentage, 0) * 100
        ) / 100;
    });

    table.setColumnSizingInfo((old) => ({
      ...old,
      startOffset,
      startSize,
      deltaOffset,
      deltaPercentage,
      columnSizingStart,
      isResizingColumn: column.id,
    }));

    if (commit) {
      table.setColumnSizing((old) => ({
        ...old,
        ...nextColumnSizing,
      }));
    }
  };

  // Single teardown path: commits at the given position, removes every
  // document/window listener, and restores cursors. Safe to call more than
  // once (blur + mouseup + unmount can race).
  const endResize = (clientXPos?: number) => {
    if (ended) return;
    ended = true;

    stopListeners.forEach((stop) => stop());
    updateOffset(clientXPos, true);
    table.setColumnSizingInfo((old) => ({
      ...old,
      isResizingColumn: false,
      startOffset: null,
      startSize: null,
      deltaOffset: null,
      deltaPercentage: null,
      columnSizingStart: [],
    }));
    ownerDocument.body.style.cursor = previousBodyCursor;
    ownerDocument.documentElement.style.cursor = previousDocumentCursor;
  };

  const mouseMoveHandler = (moveEvent: globalThis.MouseEvent) => {
    updateOffset(moveEvent.clientX);
  };
  const mouseUpHandler = (upEvent: globalThis.MouseEvent) => {
    endResize(upEvent.clientX);
  };
  const touchMoveHandler = (moveEvent: globalThis.TouchEvent) => {
    if (moveEvent.cancelable) {
      moveEvent.preventDefault();
      moveEvent.stopPropagation();
    }

    updateOffset(getDataGridResizeEventClientX(moveEvent, touchIdentifier));
  };
  const touchEndHandler = (endEvent: globalThis.TouchEvent) => {
    // Ignore other fingers lifting; only the initiating touch ends the drag.
    const clientXPos =
      typeof touchIdentifier === "number"
        ? findTouchClientX(endEvent.changedTouches, touchIdentifier)
        : getDataGridResizeEventClientX(endEvent);

    if (typeof clientXPos !== "number") return;

    if (endEvent.cancelable) {
      endEvent.preventDefault();
      endEvent.stopPropagation();
    }

    endResize(clientXPos);
  };
  // System-interrupted gestures and window focus loss would otherwise leave
  // the session (and its document listeners) live with no pointer held.
  const touchCancelHandler = () => {
    endResize(lastClientX);
  };
  const windowBlurHandler = () => {
    endResize(lastClientX);
  };

  const passiveIfSupported = { passive: false } as const;

  if (isTouchSession) {
    ownerDocument.addEventListener(
      "touchmove",
      touchMoveHandler,
      passiveIfSupported
    );
    ownerDocument.addEventListener(
      "touchend",
      touchEndHandler,
      passiveIfSupported
    );
    ownerDocument.addEventListener("touchcancel", touchCancelHandler);
    stopListeners.push(() => {
      ownerDocument.removeEventListener("touchmove", touchMoveHandler);
      ownerDocument.removeEventListener("touchend", touchEndHandler);
      ownerDocument.removeEventListener("touchcancel", touchCancelHandler);
    });
  } else {
    ownerDocument.addEventListener(
      "mousemove",
      mouseMoveHandler,
      passiveIfSupported
    );
    ownerDocument.addEventListener(
      "mouseup",
      mouseUpHandler,
      passiveIfSupported
    );
    stopListeners.push(() => {
      ownerDocument.removeEventListener("mousemove", mouseMoveHandler);
      ownerDocument.removeEventListener("mouseup", mouseUpHandler);
    });
  }

  if (ownerWindow) {
    ownerWindow.addEventListener("blur", windowBlurHandler);
    stopListeners.push(() =>
      ownerWindow.removeEventListener("blur", windowBlurHandler)
    );
  }

  table.setColumnSizingInfo((old) => ({
    ...old,
    startOffset,
    startSize,
    deltaOffset: 0,
    deltaPercentage: 0,
    columnSizingStart,
    isResizingColumn: column.id,
  }));

  return () => endResize(lastClientX);
}

type DataGridTablePinnedBoundary = "top" | "bottom";

function getDataGridTableRowSections<TData>(
  table: Table<TData>,
  rowsPinnable?: boolean
) {
  if (!rowsPinnable) {
    return {
      topRows: [] as Row<TData>[],
      centerRows: table.getRowModel().rows as Row<TData>[],
      bottomRows: [] as Row<TData>[],
    };
  }

  return {
    topRows: table.getTopRows() as Row<TData>[],
    centerRows: table.getCenterRows() as Row<TData>[],
    bottomRows: table.getBottomRows() as Row<TData>[],
  };
}

function getDataGridTableResolvedRows<TData>(
  table: Table<TData>,
  rowsPinnable?: boolean
) {
  const { topRows, centerRows, bottomRows } = getDataGridTableRowSections(
    table,
    rowsPinnable
  );
  const resolvedRows: Array<{
    row: Row<TData>;
    pinnedBoundary?: DataGridTablePinnedBoundary;
  }> = [];

  topRows.forEach((row, index) => {
    resolvedRows.push({
      row,
      pinnedBoundary:
        index === topRows.length - 1 &&
        (centerRows.length > 0 || bottomRows.length > 0)
          ? "top"
          : undefined,
    });
  });

  centerRows.forEach((row) => {
    resolvedRows.push({ row });
  });

  bottomRows.forEach((row, index) => {
    resolvedRows.push({
      row,
      pinnedBoundary:
        index === 0 && (centerRows.length > 0 || topRows.length > 0)
          ? "bottom"
          : undefined,
    });
  });

  return resolvedRows;
}

function getDataGridTableOrderedVisibleColumns<TData>(table: Table<TData>) {
  return [
    ...table.getLeftVisibleLeafColumns(),
    ...table.getCenterVisibleLeafColumns(),
    ...table.getRightVisibleLeafColumns(),
  ] as Column<TData>[];
}

function getDataGridTableOrderedVisibleCells<TData>(row: Row<TData>) {
  return [
    ...row.getLeftVisibleCells(),
    ...row.getCenterVisibleCells(),
    ...row.getRightVisibleCells(),
  ] as Cell<TData, unknown>[];
}

function getDataGridTableMergedHeaderGroups<TData>(table: Table<TData>) {
  const leftHeaderGroups = table.getLeftHeaderGroups();
  const centerHeaderGroups = table.getCenterHeaderGroups();
  const rightHeaderGroups = table.getRightHeaderGroups();
  const headerGroupCount = Math.max(
    leftHeaderGroups.length,
    centerHeaderGroups.length,
    rightHeaderGroups.length
  );

  return Array.from({ length: headerGroupCount }, (_, index) => {
    const leftGroup = leftHeaderGroups[index];
    const centerGroup = centerHeaderGroups[index];
    const rightGroup = rightHeaderGroups[index];

    return {
      id:
        [leftGroup?.id, centerGroup?.id, rightGroup?.id]
          .filter(Boolean)
          .join(":") || `header-group-${index}`,
      headers: [
        ...(leftGroup?.headers ?? []),
        ...(centerGroup?.headers ?? []),
        ...(rightGroup?.headers ?? []),
      ] as Header<TData, unknown>[],
    };
  });
}

function hasDataGridTableRightPinnedColumns<TData>(table: Table<TData>) {
  return (table.getState().columnPinning.right?.length ?? 0) > 0;
}

function DataGridTableFillCol() {
  const { props } = useDataGrid();

  if (!props.tableLayout?.columnsResizable) return null;

  return (
    <col
      data-slot="data-grid-table-fill-col"
      style={{ width: "var(--data-grid-fill-size, 0px)" }}
    />
  );
}

function DataGridTableFillHeadCell() {
  const { props } = useDataGrid();

  if (!props.tableLayout?.columnsResizable) return null;

  return (
    <th
      aria-hidden="true"
      data-slot="data-grid-table-fill-head-cell"
      style={{ width: "var(--data-grid-fill-size, 0px)" }}
      className={cn("p-0", props.tableLayout?.headerBackground && "bg-muted")}
    />
  );
}

function DataGridTableFillBodyCell() {
  const { props } = useDataGrid();

  if (!props.tableLayout?.columnsResizable) return null;

  return (
    <td
      aria-hidden="true"
      data-slot="data-grid-table-fill-body-cell"
      style={{ width: "var(--data-grid-fill-size, 0px)" }}
      className="p-0"
    />
  );
}

function DataGridTableFillFootCell() {
  const { props } = useDataGrid();

  if (!props.tableLayout?.columnsResizable) return null;

  return (
    <td
      aria-hidden="true"
      data-slot="data-grid-table-fill-foot-cell"
      style={{ width: "var(--data-grid-fill-size, 0px)" }}
      className="p-0"
    />
  );
}

function DataGridTableBase({ children }: { children: ReactNode }) {
  const { isLoading, props, table } = useDataGrid();
  const leftVisibleColumns = table.getLeftVisibleLeafColumns();
  const centerVisibleColumns = table.getCenterVisibleLeafColumns();
  const rightVisibleColumns = table.getRightVisibleLeafColumns();
  const hasRightPinnedColumns = hasDataGridTableRightPinnedColumns(table);

  /*
   * Column widths as CSS custom properties, computed once per render. Cells
   * reference them via calc(var(--col-X-size) * 1px) so the browser handles
   * width propagation without per-cell getSize() calls.
   *
   * DEVIATION from ReUI: upstream memoizes this on `table.getState().columnX`
   * slices, which `react-hooks/use-memo` rejects (dependencies must be simple
   * expressions). The loop is O(visible columns) and every input that changes
   * it also re-renders this component, so the memo bought nothing a correct
   * dependency list could keep.
   */
  const columnSizeVars = ((): Record<string, number> | undefined => {
    if (!props.tableLayout?.columnsResizable) return undefined;
    const headers = table.getFlatHeaders();
    const colSizes: Record<string, number> = {};
    for (const header of headers) {
      colSizes[`--header-${header.id}-size`] = header.getSize();
      colSizes[`--col-${header.column.id}-size`] = header.column.getSize();
    }
    return colSizes;
  })();

  return (
    <table
      data-slot="data-grid-table"
      aria-busy={isLoading || undefined}
      className={cn(
        "caption-bottom text-left align-middle text-body font-normal text-ink rtl:text-right",
        props.tableLayout?.columnsResizable ? "min-w-0" : "w-full min-w-full",
        props.tableLayout?.width === "auto" ? "table-auto" : "table-fixed",
        !props.tableLayout?.columnsDraggable &&
          "border-separate border-spacing-0",
        props.tableClassNames?.base
      )}
      style={
        props.tableLayout?.columnsResizable
          ? {
              ...columnSizeVars,
              width: `calc(${table.getTotalSize()}px + var(--data-grid-fill-size, 0px))`,
            }
          : undefined
      }
    >
      <colgroup>
        {[...leftVisibleColumns, ...centerVisibleColumns].map((column) => (
          <col
            key={column.id}
            style={
              props.tableLayout?.columnsResizable
                ? { width: `calc(var(--col-${column.id}-size) * 1px)` }
                : props.tableLayout?.width === "fixed"
                  ? { width: column.getSize() }
                  : undefined
            }
          />
        ))}
        {hasRightPinnedColumns ? <DataGridTableFillCol /> : null}
        {rightVisibleColumns.map((column) => (
          <col
            key={column.id}
            style={
              props.tableLayout?.columnsResizable
                ? { width: `calc(var(--col-${column.id}-size) * 1px)` }
                : props.tableLayout?.width === "fixed"
                  ? { width: column.getSize() }
                  : undefined
            }
          />
        ))}
        {!hasRightPinnedColumns ? <DataGridTableFillCol /> : null}
      </colgroup>
      {children}
    </table>
  );
}

function DataGridTableResizeIndicator({
  viewportNodeRef,
}: {
  viewportNodeRef: RefObject<HTMLDivElement | null>;
}) {
  const { props, table } = useDataGrid();
  const indicatorRef = useRef<HTMLDivElement | null>(null);
  const indicatorHeadRef = useRef<HTMLDivElement | null>(null);
  // Header height is stable for the duration of a drag; caching it per
  // session avoids a forced layout (querySelector + getBoundingClientRect)
  // on every mousemove.
  const headerHeightCacheRef = useRef<{
    key: string | false;
    value: number;
  }>({ key: false, value: 0 });
  const columnSizingInfo = table.getState().columnSizingInfo;
  const resizingColumnId = columnSizingInfo.isResizingColumn;
  const resizeMode =
    props.tableLayout?.columnsResizeMode ?? table.options.columnResizeMode;
  const isActive = !!(
    props.tableLayout?.columnsResizable &&
    resizeMode === "onEnd" &&
    resizingColumnId
  );

  // Positioning happens imperatively after each drag-frame render: layout
  // reads (viewport rect, thead height) and ref access belong outside render,
  // and writing styles directly avoids holding the viewport node in React
  // state, which would cost every grid a second render pass at mount.
  useLayoutEffect(() => {
    const indicator = indicatorRef.current;
    const indicatorHead = indicatorHeadRef.current;
    const viewportElement = viewportNodeRef.current;

    if (!isActive || !indicator || !indicatorHead || !resizingColumnId) return;

    const resizingHeader = table
      .getFlatHeaders()
      .find(
        (header) =>
          header.column.id === resizingColumnId ||
          header.id === resizingColumnId
      );

    if (!resizingHeader) return;

    // deltaOffset is a logical delta (already direction-adjusted); translate
    // by the physical pointer movement so the indicator follows the cursor
    // in RTL instead of mirroring it.
    const directionMultiplier =
      table.options.columnResizeDirection === "rtl" ? -1 : 1;
    const deltaOffset =
      (columnSizingInfo.deltaOffset ?? 0) * directionMultiplier;

    if (headerHeightCacheRef.current.key !== resizingColumnId) {
      headerHeightCacheRef.current = {
        key: resizingColumnId,
        value:
          viewportElement
            ?.querySelector('[data-slot="data-grid-table"] thead')
            ?.getBoundingClientRect().height ?? 0,
      };
    }

    const headerHeight = headerHeightCacheRef.current.value;
    const indicatorLeft =
      typeof columnSizingInfo.startOffset === "number" && viewportElement
        ? columnSizingInfo.startOffset -
          viewportElement.getBoundingClientRect().left
        : resizingHeader.getStart() + resizingHeader.getSize();

    indicator.style.left = `${indicatorLeft}px`;
    indicator.style.transform = `translateX(${deltaOffset}px)`;
    indicatorHead.style.height = `${Math.max(headerHeight, 6)}px`;
  });

  if (!isActive) return null;

  return (
    <div
      ref={indicatorRef}
      aria-hidden="true"
      className="pointer-events-none absolute inset-y-0 z-50"
    >
      <div className="absolute inset-y-0 left-0 w-px -translate-x-1/2 bg-primary" />
      <div
        ref={indicatorHeadRef}
        className="absolute top-0 left-0 -translate-x-1/2 rounded-b-compact bg-primary"
        style={{ width: 5 }}
      />
    </div>
  );
}

function DataGridTableViewport({
  children,
  className,
  viewportRef,
  style,
}: {
  children: ReactNode;
  className?: string;
  viewportRef?: Ref<HTMLDivElement>;
  style?: CSSProperties;
}) {
  const { props, table, autoSize } = useDataGrid();
  const isColumnsResizable = !!props.tableLayout?.columnsResizable;
  const viewportNodeRef = useRef<HTMLDivElement | null>(null);
  const fillStateRef = useRef({ containerWidth: 0, appliedFill: -1 });
  const stopContainerObserverRef = useRef<(() => void) | null>(null);

  // Free space is written as a CSS variable directly on the viewport node
  // instead of React state, so container resizes and column-size commits
  // reach the fill column without re-rendering the grid.
  const syncFillWidth = useCallback(() => {
    const node = viewportNodeRef.current;
    if (!node) return;

    const fillWidth = Math.max(
      0,
      fillStateRef.current.containerWidth - table.getTotalSize()
    );

    if (fillStateRef.current.appliedFill !== fillWidth) {
      fillStateRef.current.appliedFill = fillWidth;
      node.style.setProperty("--data-grid-fill-size", `${fillWidth}px`);
    }

    autoSize?.apply(fillWidth);
  }, [autoSize, table]);

  const handleViewportRef = useCallback(
    (node: HTMLDivElement | null) => {
      stopContainerObserverRef.current?.();
      stopContainerObserverRef.current = null;
      viewportNodeRef.current = node;
      assignRef(viewportRef, node);

      if (!node) return;

      if (!isColumnsResizable) {
        fillStateRef.current.appliedFill = -1;
        node.style.removeProperty("--data-grid-fill-size");
        return;
      }

      const scrollViewport =
        getDataGridScrollAreaViewport(node) ?? node.parentElement;
      const measurementTarget = scrollViewport ?? node;

      const measure = () => {
        fillStateRef.current.containerWidth = measurementTarget.clientWidth;
        syncFillWidth();
      };

      // First measure runs inside the mount commit, before paint, so the fill
      // column and any meta.autoSize growth land in the first painted frame.
      measure();

      if (typeof ResizeObserver !== "undefined") {
        const observer = new ResizeObserver(measure);
        observer.observe(measurementTarget);
        stopContainerObserverRef.current = () => observer.disconnect();
      }
    },
    [isColumnsResizable, syncFillWidth, viewportRef]
  );

  // Column sizing commits and visibility changes alter the table's total size
  // without moving the container, so the fill var must re-sync after renders
  // the ResizeObserver never sees. No-ops when the value is unchanged.
  useLayoutEffect(() => {
    if (!isColumnsResizable) return;
    syncFillWidth();
  });

  return (
    <div
      data-slot="data-grid-table-viewport"
      ref={handleViewportRef}
      className={cn("relative min-w-full align-top", className)}
      style={{
        ...(isColumnsResizable
          ? {
              width: `calc(${table.getTotalSize()}px + var(--data-grid-fill-size, 0px))`,
            }
          : undefined),
        ...style,
      }}
    >
      {children}
      <DataGridTableResizeIndicator viewportNodeRef={viewportNodeRef} />
    </div>
  );
}

function DataGridTableHead({ children }: { children: ReactNode }) {
  const { props } = useDataGrid();

  return (
    <thead
      className={cn(
        props.tableClassNames?.header,
        props.tableLayout?.headerSticky && props.tableClassNames?.headerSticky
      )}
    >
      {children}
    </thead>
  );
}

function DataGridTableHeadRow({
  children,
}: {
  children: ReactNode;
  /** Accepted for API compatibility with ReUI; the row is keyed by the caller. */
  rowId?: string;
}) {
  const { props } = useDataGrid();

  return (
    <tr
      className={cn(
        props.tableLayout?.headerBorder && "[&>th]:border-b [&>th]:border-line",
        props.tableLayout?.cellBorder && "*:last:border-e-0",
        props.tableLayout?.stripped && "bg-transparent",
        props.tableLayout?.headerBackground === false && "bg-transparent",
        props.tableClassNames?.headerRow
      )}
    >
      {children}
    </tr>
  );
}

function DataGridTableHeadRowCell<TData>({
  children,
  header,
  dndRef,
  dndStyle,
}: {
  children: ReactNode;
  header: Header<TData, unknown>;
  dndRef?: Ref<HTMLTableCellElement>;
  dndStyle?: CSSProperties;
}) {
  const { props } = useDataGrid<TData>();

  const { column } = header;
  const isPinned = column.getIsPinned();
  const isFirstLeftPinned =
    isPinned === "left" && column.getIsFirstColumn("left");
  const isLastLeftPinned = isPinned === "left" && column.getIsLastColumn("left");
  const isFirstRightPinned =
    isPinned === "right" && column.getIsFirstColumn("right");
  const isLastRightPinned =
    isPinned === "right" && column.getIsLastColumn("right");
  const isLastVisibleColumn =
    column.getIndex() ===
    header.getContext().table.getVisibleLeafColumns().length - 1;
  const headerCellSpacing = headerCellSpacingVariants({
    size: props.tableLayout?.dense ? "dense" : "default",
  });

  const sortDirection = column.getIsSorted();

  return (
    <th
      ref={dndRef}
      scope="col"
      colSpan={header.colSpan > 1 ? header.colSpan : undefined}
      aria-sort={
        sortDirection === "asc"
          ? "ascending"
          : sortDirection === "desc"
            ? "descending"
            : undefined
      }
      style={{
        ...(props.tableLayout?.width === "fixed" &&
          !props.tableLayout?.columnsResizable && {
            width: header.getSize(),
          }),
        ...(props.tableLayout?.columnsPinnable &&
          column.getCanPin() &&
          getPinningStyles(column)),
        ...(props.tableLayout?.columnsResizable && {
          width: `calc(var(--header-${header.id}-size) * 1px)`,
        }),
        ...(dndStyle ? dndStyle : null),
      }}
      data-pinned={isPinned || undefined}
      data-outer-pinned-col={
        isFirstLeftPinned ? "left" : isLastRightPinned ? "right" : undefined
      }
      data-last-col={
        isLastLeftPinned ? "left" : isFirstRightPinned ? "right" : undefined
      }
      className={cn(
        "relative h-row-data text-left align-middle font-medium text-ink rtl:text-right",
        headerCellSpacing,
        props.tableLayout?.headerBackground && "bg-muted",
        props.tableLayout?.cellBorder && "border-e border-line",
        props.tableLayout?.columnsResizable &&
          column.getCanResize() &&
          "overflow-hidden",
        props.tableLayout?.columnsResizable &&
          column.getCanResize() &&
          isLastVisibleColumn &&
          "pe-8",
        props.tableLayout?.columnsPinnable &&
          column.getCanPin() &&
          cn(
            "data-pinned:isolate data-pinned:bg-muted data-outer-pinned-col:bg-clip-padding",
            PINNED_EDGE_CLASS,
            "[&[data-pinned=right]:last-child_div.cursor-col-resize:last-child]:opacity-0",
            "[&:not([data-pinned]):has(+[data-pinned])_div.cursor-col-resize:last-child]:opacity-0 [&[data-last-col=left]_div.cursor-col-resize:last-child]:opacity-0"
          ),
        header.column.columnDef.meta?.headerClassName,
        // Edge detection spans the full visible leaf order; the header's own
        // group only covers one pinning bucket.
        column.getIndex() === 0 || isLastVisibleColumn
          ? props.tableClassNames?.edgeCell
          : ""
      )}
    >
      {children}
    </th>
  );
}

function DataGridTableHeadRowCellResize<TData>({
  header,
}: {
  header: Header<TData, unknown>;
}) {
  const { props, table } = useDataGrid<TData>();
  const { column } = header;
  const isPinned = column.getIsPinned();
  const isLastVisibleColumn =
    column.getIndex() ===
    header.getContext().table.getVisibleLeafColumns().length - 1;
  const isResizeModeOnEnd =
    (props.tableLayout?.columnsResizeMode ?? table.options.columnResizeMode) ===
    "onEnd";
  const stopResizeSessionRef = useRef<(() => void) | undefined>(undefined);

  // End a live drag if the handle unmounts mid-resize so document listeners
  // and the app-wide col-resize cursor don't outlive the grid.
  useEffect(() => {
    return () => {
      stopResizeSessionRef.current?.();
      stopResizeSessionRef.current = undefined;
    };
  }, []);

  const handleMouseDown = (event: ReactMouseEvent<HTMLDivElement>) => {
    // Only the primary button starts a resize; guard before preventDefault so
    // right-click still opens the context menu.
    if (event.button !== 0) return;

    event.preventDefault();
    event.stopPropagation();

    if (isResizeModeOnEnd) {
      stopResizeSessionRef.current?.();
      stopResizeSessionRef.current = startDataGridColumnResizeOnEnd(
        event,
        header,
        table
      );
      return;
    }

    header.getResizeHandler()(event);
  };

  const handleTouchStart = (event: ReactTouchEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();

    if (isResizeModeOnEnd) {
      stopResizeSessionRef.current?.();
      stopResizeSessionRef.current = startDataGridColumnResizeOnEnd(
        event,
        header,
        table
      );
      return;
    }

    header.getResizeHandler()(event);
  };

  return (
    <div
      onDoubleClick={() => column.resetSize()}
      onMouseDown={handleMouseDown}
      onTouchStart={handleTouchStart}
      className={cn(
        "absolute top-0 z-10 flex h-full cursor-col-resize touch-none select-none",
        isLastVisibleColumn
          ? "end-0 w-5 justify-end before:hidden"
          : isPinned
            ? cn(
                // A pinned column is sticky, so the handle sits inside the
                // cell instead of straddling the boundary, where the next
                // sticky cell would paint over it.
                "end-0 w-5 justify-end",
                // With the pin affordance on, the pinned edge already draws
                // its own separator and a resize line would double it. But
                // pinning is also usable purely as an ordering lock, with no
                // affordance and no separator -- and there this line is the
                // only thing marking the edge, so hiding it left a resizable
                // column showing a resize cursor and no indicator at all.
                props.tableLayout?.columnsPinnable
                  ? "before:hidden"
                  : "before:absolute before:inset-y-0 before:end-0 before:w-px before:bg-line-strong"
              )
            : "-end-2 w-5 justify-center before:absolute before:inset-y-0 before:w-px before:-translate-x-px before:bg-line-strong",
        column.getIsResizing() &&
          (isResizeModeOnEnd
            ? "opacity-100"
            : isLastVisibleColumn
              ? "opacity-100 before:absolute before:inset-y-0 before:end-0 before:block before:w-0.5 before:bg-primary"
              : "opacity-100 before:block before:w-0.5 before:bg-primary")
      )}
    />
  );
}

function DataGridTableRowSpacer() {
  return (
    <tbody
      aria-hidden="true"
      className="h-2"
      data-slot="data-grid-table-body-spacer"
    ></tbody>
  );
}

function DataGridTableBody({ children }: { children: ReactNode }) {
  const { props } = useDataGrid();

  return (
    <tbody
      data-slot="data-grid-table-body"
      className={cn(
        props.tableLayout?.rowRounded && "[&_td:first-child]:rounded-l-panel",
        props.tableLayout?.rowRounded && "[&_td:last-child]:rounded-r-panel",
        props.tableClassNames?.body
      )}
    >
      {children}
    </tbody>
  );
}

function DataGridTableFoot({ children }: { children: ReactNode }) {
  const { props } = useDataGrid();
  return (
    <tfoot
      data-slot="data-grid-table-foot"
      className={cn(props.tableClassNames?.footer)}
    >
      {children}
    </tfoot>
  );
}

function DataGridTableFootRow({ children }: { children: ReactNode }) {
  const { props } = useDataGrid();
  const footRowBottomBorderClasses =
    "[&:not(:last-child)>td]:border-b [&:not(:last-child)>td]:border-line";

  return (
    <tr
      data-slot="data-grid-table-foot-row"
      className={cn(
        props.tableLayout?.footerBackground && "bg-muted",
        props.tableLayout?.rowBorder && footRowBottomBorderClasses,
        props.tableLayout?.cellBorder && "*:last:border-e-0"
      )}
    >
      {children}
      <DataGridTableFillFootCell />
    </tr>
  );
}

function DataGridTableFootRowCell({
  children,
  colSpan,
  className,
}: {
  children?: ReactNode;
  colSpan?: number;
  className?: string;
}) {
  const { props } = useDataGrid();
  const spacing = footerCellSpacingVariants({
    size: props.tableLayout?.dense ? "dense" : "default",
  });
  return (
    <td
      colSpan={colSpan}
      className={cn(
        "align-middle font-medium text-ink-muted",
        spacing,
        props.tableLayout?.footerBackground && "bg-muted",
        props.tableLayout?.cellBorder && "border-e border-line",
        className
      )}
    >
      {children}
    </td>
  );
}

function DataGridTableBodyRowSkeleton({ children }: { children: ReactNode }) {
  const { table, props } = useDataGrid();

  return (
    <tr
      data-slot="data-grid-skeleton-row"
      className={cn(
        "hover:bg-hover data-[state=selected]:bg-selected",
        props.onRowClick && "cursor-pointer",
        !props.tableLayout?.stripped &&
          props.tableLayout?.rowBorder &&
          "border-b border-line [&:not(:last-child)>td]:border-b [&:not(:last-child)>td]:border-line",
        props.tableLayout?.cellBorder && "*:last:border-e-0",
        props.tableLayout?.stripped &&
          "odd:bg-muted hover:bg-transparent odd:hover:bg-hover",
        table.options.enableRowSelection && "*:first:relative",
        props.tableClassNames?.bodyRow
      )}
    >
      {children}
    </tr>
  );
}

function DataGridTableBodyRowSkeletonCell<TData>({
  children,
  column,
}: {
  children: ReactNode;
  column: Column<TData>;
}) {
  const { props, table } = useDataGrid<TData>();
  const isPinned = column.getIsPinned();
  const isLastLeftPinned = isPinned === "left" && column.getIsLastColumn("left");
  const isFirstRightPinned =
    isPinned === "right" && column.getIsFirstColumn("right");
  const bodyCellSpacing = bodyCellSpacingVariants({
    size: props.tableLayout?.dense ? "dense" : "default",
  });

  return (
    <td
      style={{
        ...(props.tableLayout?.columnsPinnable &&
          column.getCanPin() &&
          getPinningStyles(column)),
        ...(props.tableLayout?.columnsResizable && {
          width: `calc(var(--col-${column.id}-size) * 1px)`,
        }),
      }}
      data-pinned={isPinned || undefined}
      data-last-col={
        isLastLeftPinned ? "left" : isFirstRightPinned ? "right" : undefined
      }
      className={cn(
        "align-middle",
        bodyCellSpacing,
        props.tableLayout?.cellBorder && "border-e border-line",
        props.tableLayout?.columnsResizable &&
          column.getCanResize() &&
          "truncate",
        column.columnDef.meta?.cellClassName,
        props.tableLayout?.columnsPinnable &&
          column.getCanPin() &&
          cn("data-pinned:isolate data-pinned:bg-panel", PINNED_EDGE_CLASS),
        column.getIndex() === 0 ||
          column.getIndex() === table.getVisibleLeafColumns().length - 1
          ? props.tableClassNames?.edgeCell
          : ""
      )}
    >
      {children ?? <DefaultSkeletonCell columnIndex={column.getIndex()} />}
    </td>
  );
}

function DataGridTableBodyRow<TData>({
  children,
  row,
  pinnedBoundary,
  rowRef,
  dndRef,
  dndStyle,
  dataIndex,
}: {
  children: ReactNode;
  row: Row<TData>;
  pinnedBoundary?: DataGridTablePinnedBoundary;
  rowRef?: Ref<HTMLTableRowElement>;
  dndRef?: Ref<HTMLTableRowElement>;
  dndStyle?: CSSProperties;
  dataIndex?: number;
}) {
  const { props, table } = useDataGrid<TData>();
  const isRowPinned = row.getIsPinned();
  const isGrouped = row.getIsGrouped();
  const isSelected =
    (table.options.enableRowSelection && row.getIsSelected()) ||
    (!isGrouped &&
      props.selectedRowId !== undefined &&
      row.id === props.selectedRowId);
  const rowIsClickable =
    Boolean(props.onRowClick) &&
    !isGrouped &&
    (props.isRowClickable?.(row.original) ?? true);
  const rowIsFresh = !isGrouped && props.isRowFresh?.(row.original) === true;

  const bodyRowBottomBorderClasses =
    "[&:not(:last-child)>td]:border-b [&:not(:last-child)>td]:border-line [tbody:has(+tfoot)_&:last-child>td]:border-b [*:has(>[data-slot=data-grid]+[data-slot=data-grid-pagination])_[data-slot=data-grid]_&:last-child>td]:border-b";

  return (
    <tr
      ref={(node) => {
        assignRef(rowRef, node);
        assignRef(dndRef, node);
      }}
      style={{ ...(dndStyle ? dndStyle : null) }}
      data-state={isSelected ? "selected" : undefined}
      data-fresh={rowIsFresh ? "true" : undefined}
      data-index={dataIndex}
      data-row-id={row.id}
      data-depth={row.depth || undefined}
      data-grouped={isGrouped || undefined}
      data-row-pinned={isRowPinned || undefined}
      data-row-pinned-boundary={pinnedBoundary}
      onClick={() => {
        if (!rowIsClickable || props.onRowClick === undefined) return;
        props.onRowClick(row.original);
      }}
      className={cn(
        "group/data-row hover:bg-hover data-[state=selected]:bg-selected",
        rowIsClickable && "cursor-pointer",
        props.isRowFresh !== undefined &&
          "transition-colors duration-fast ease-out-quint data-[fresh=true]:bg-info-bg data-[fresh=true]:transition-none motion-reduce:transition-none",
        !props.tableLayout?.stripped &&
          props.tableLayout?.rowBorder &&
          bodyRowBottomBorderClasses,
        props.tableLayout?.cellBorder &&
          `*:last:border-e-0 ${bodyRowBottomBorderClasses}`,
        // Virtualized rows stripe by absolute row index (CSS :nth-child
        // parity flips as spacer rows resize while scrolling).
        props.tableLayout?.stripped &&
          (typeof dataIndex === "number"
            ? cn(
                "hover:bg-transparent",
                dataIndex % 2 === 0 && "bg-muted hover:bg-hover"
              )
            : "odd:bg-muted hover:bg-transparent odd:hover:bg-hover"),
        table.options.enableRowSelection && "*:first:relative",
        props.tableLayout?.rowsPinnable &&
          isRowPinned &&
          "bg-selected hover:bg-hover",
        pinnedBoundary === "top" && PINNED_ROW_BOUNDARY_CLASS,
        pinnedBoundary === "bottom" && PINNED_ROW_BOUNDARY_CLASS,
        props.tableClassNames?.bodyRow
      )}
    >
      {children}
    </tr>
  );
}

type ExpandRender<TData> = (row: TData) => ReactNode;

function columnExpandRender<TData>(
  table: Table<TData>
): { mode: "rows" | "panel"; render: ExpandRender<TData> } | null {
  let panel: ExpandRender<TData> | undefined;
  for (const column of table.getAllColumns()) {
    const rows = column.columnDef.meta?.expandedRows;
    if (rows !== undefined) return { mode: "rows", render: rows };
    panel ??= column.columnDef.meta?.expandedContent;
  }
  if (panel !== undefined) return { mode: "panel", render: panel };
  return null;
}

function DataGridTableBodyRowExpandded<TData>({ row }: { row: Row<TData> }) {
  const { props, table } = useDataGrid<TData>();
  const expand = columnExpandRender(table);

  // Tree and grouped rows share row.getIsExpanded() with detail expansion.
  // Without a detail column there is nothing to render, and an empty <tr>
  // would break striping parity, rowBorder, and virtual row measurement.
  if (expand === null) return null;

  if (expand.mode === "rows") {
    return expand.render(row.original);
  }

  const bodyRowBottomBorderClasses =
    "[&:not(:last-child)>td]:border-b [&:not(:last-child)>td]:border-line [tbody:has(+tfoot)_&:last-child>td]:border-b [*:has(>[data-slot=data-grid]+[data-slot=data-grid-pagination])_[data-slot=data-grid]_&:last-child>td]:border-b";

  return (
    <tr
      className={cn(
        props.tableLayout?.rowBorder && bodyRowBottomBorderClasses
      )}
    >
      <td
        colSpan={
          getDataGridTableOrderedVisibleCells(row).length +
          (props.tableLayout?.columnsResizable ? 1 : 0)
        }
      >
        {expand.render(row.original)}
      </td>
    </tr>
  );
}

function DataGridTableBodyRowCell<TData>({
  children,
  column,
  dndRef,
  dndStyle,
}: {
  children: ReactNode;
  column: Column<TData, unknown>;
  dndRef?: Ref<HTMLTableCellElement>;
  dndStyle?: CSSProperties;
}) {
  const { props, table } = useDataGrid<TData>();
  const isPinned = column.getIsPinned();
  const isLastLeftPinned = isPinned === "left" && column.getIsLastColumn("left");
  const isFirstRightPinned =
    isPinned === "right" && column.getIsFirstColumn("right");
  const bodyCellSpacing = bodyCellSpacingVariants({
    size: props.tableLayout?.dense ? "dense" : "default",
  });
  const lastVisibleIndex =
    getDataGridTableOrderedVisibleColumns(table).length - 1;

  return (
    <td
      ref={dndRef}
      style={{
        ...(props.tableLayout?.columnsPinnable &&
          column.getCanPin() &&
          getPinningStyles(column)),
        ...(props.tableLayout?.columnsResizable && {
          width: `calc(var(--col-${column.id}-size) * 1px)`,
        }),
        ...(dndStyle ? dndStyle : null),
      }}
      data-pinned={isPinned || undefined}
      data-last-col={
        isLastLeftPinned ? "left" : isFirstRightPinned ? "right" : undefined
      }
      className={cn(
        "align-middle",
        bodyCellSpacing,
        props.tableLayout?.cellBorder && "border-e border-line",
        props.tableLayout?.columnsResizable &&
          column.getCanResize() &&
          "truncate",
        column.columnDef.meta?.cellClassName,
        props.tableLayout?.columnsPinnable &&
          column.getCanPin() &&
          cn("data-pinned:isolate data-pinned:bg-panel", PINNED_EDGE_CLASS),
        column.getIndex() === 0 || column.getIndex() === lastVisibleIndex
          ? props.tableClassNames?.edgeCell
          : ""
      )}
    >
      {children}
    </td>
  );
}

function DataGridTableGroupedCell<TData>({
  cell,
}: {
  cell: Cell<TData, unknown>;
}) {
  const { row } = cell;
  const rendered = flexRender(cell.column.columnDef.cell, cell.getContext());

  if (row.getIsGrouped()) {
    if (isGroupedRowLeadCell(cell)) {
      return <DataGridTableGroupedLead row={row} />;
    }
    if (cell.getIsGrouped()) return null;
    if (cell.getIsAggregated()) {
      return flexRender(
        cell.column.columnDef.aggregatedCell ?? cell.column.columnDef.cell,
        cell.getContext()
      );
    }
    return null;
  }

  const firstVisible = row.getVisibleCells()[0];
  if (row.depth > 0 && firstVisible !== undefined && cell.id === firstVisible.id) {
    return (
      <Inline gap="sm" align="center" className="min-w-0">
        <DataGridTableRowExpand row={row} />
        {rendered}
      </Inline>
    );
  }

  return rendered;
}

/** Checkbox column FilterableTable prepends. Group labels skip it so they hug the left edge. */
const SELECT_COLUMN_ID = "select";

interface DataGridServerGroup {
  count: number;
  expanded: boolean;
  hasMore: boolean;
  id: string;
  isFetchingMore: boolean;
  isLoading: boolean;
  label: ReactNode;
  /** Plain server label for accessible copy outside the visual group header. */
  labelText: string;
  onFetchMore: () => void;
  onToggle: () => void;
}

interface DataGridServerGrouping {
  groups: readonly DataGridServerGroup[];
  hasMore: boolean;
  isFetchingMore: boolean;
  onFetchMore: () => void;
}

function isGroupedRowLeadCell<TData>(cell: Cell<TData, unknown>): boolean {
  const lead = cell.row
    .getVisibleCells()
    .find((entry) => entry.column.id !== SELECT_COLUMN_ID);
  return lead !== undefined && cell.id === lead.id;
}

function DataGridTableGroupedLead<TData>({ row }: { row: Row<TData> }) {
  const { props } = useDataGrid<TData>();
  const groupedCell = row.getAllCells().find((entry) => entry.getIsGrouped());
  const groupedRendered =
    groupedCell === undefined
      ? null
      : flexRender(
          groupedCell.column.columnDef.cell,
          groupedCell.getContext()
        );

  return (
    <GroupRowContent
      compact={props.tableLayout?.dense}
      count={row.getLeafRows().length}
      depth={row.depth}
      expanded={row.getIsExpanded()}
      label={groupedRendered}
      onToggle={() => row.toggleExpanded()}
    />
  );
}

function DataGridTableRenderedRow<TData>({
  row,
  pinnedBoundary,
  rowRef,
  rowIndex,
}: {
  row: Row<TData>;
  pinnedBoundary?: DataGridTablePinnedBoundary;
  rowRef?: Ref<HTMLTableRowElement>;
  /** Virtualized list index, rendered as data-index for measureElement. */
  rowIndex?: number;
}) {
  const { props, table } = useDataGrid<TData>();
  const leftVisibleCells = row.getLeftVisibleCells();
  const centerVisibleCells = row.getCenterVisibleCells();
  const rightVisibleCells = row.getRightVisibleCells();
  const hasRightPinnedColumns = hasDataGridTableRightPinnedColumns(table);

  return (
    <Fragment>
      <DataGridTableBodyRow
        row={row}
        pinnedBoundary={pinnedBoundary}
        rowRef={rowRef}
        dataIndex={rowIndex}
      >
        {[...leftVisibleCells, ...centerVisibleCells].map(
          (cell: Cell<TData, unknown>) => (
            <DataGridTableBodyRowCell column={cell.column} key={cell.id}>
              <DataGridTableGroupedCell cell={cell} />
            </DataGridTableBodyRowCell>
          )
        )}
        {props.tableLayout?.columnsResizable && hasRightPinnedColumns ? (
          <DataGridTableFillBodyCell />
        ) : null}
        {rightVisibleCells.map((cell: Cell<TData, unknown>) => (
          <DataGridTableBodyRowCell column={cell.column} key={cell.id}>
            <DataGridTableGroupedCell cell={cell} />
          </DataGridTableBodyRowCell>
        ))}
        {props.tableLayout?.columnsResizable && !hasRightPinnedColumns ? (
          <DataGridTableFillBodyCell />
        ) : null}
      </DataGridTableBodyRow>
      {row.getIsExpanded() ? <DataGridTableBodyRowExpandded row={row} /> : null}
    </Fragment>
  );
}

function DataGridTableEmpty() {
  const { table, props } = useDataGrid();
  const visibleColumnCount =
    getDataGridTableOrderedVisibleColumns(table).length +
    (props.tableLayout?.columnsResizable ? 1 : 0);

  return (
    <tr>
      <td
        colSpan={Math.max(visibleColumnCount, 1)}
        className="min-h-32 py-12 text-center text-body text-ink-subtle"
      >
        {props.emptyMessage || "No data available"}
      </td>
    </tr>
  );
}

function DataGridTableLoader() {
  const { props } = useDataGrid();

  return (
    <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2">
      <div className="flex items-center gap-2 rounded-panel border border-line bg-panel px-4 py-2 text-body leading-none font-medium text-ink-subtle">
        <Spinner />
        {props.loadingMessage || "Loading..."}
      </div>
    </div>
  );
}

function DataGridTableRowPin<TData>({ row }: { row: Row<TData> }) {
  const isPinned = row.getIsPinned();

  return (
    <button
      type="button"
      aria-label={isPinned ? "Unpin row" : "Pin row"}
      onClick={(event) => {
        // Pinning must not bubble into the row's onRowClick handler.
        event.stopPropagation();

        if (isPinned) {
          row.pin(false);
        } else {
          row.pin("top");
        }
      }}
      className={cn(
        "inline-flex size-control-sm items-center justify-center rounded-control text-ink-subtle transition-[scale,color] duration-fast ease-out-quint hover:text-ink active:scale-[0.97] motion-reduce:transition-none",
        isPinned && "text-primary"
      )}
    >
      {isPinned ? <PinFilledGlyph /> : <PinGlyph />}
    </button>
  );
}

function DataGridTableRowSelect<TData>({ row }: { row: Row<TData> }) {
  return (
    <>
      <div
        className={cn(
          "absolute inset-s-0 top-0 bottom-0 hidden w-0.5 bg-primary",
          row.getIsSelected() && "block"
        )}
      ></div>
      <Checkbox
        checked={row.getIsSelected()}
        indeterminate={row.getIsSomeSelected() && !row.getIsSelected()}
        onCheckedChange={(value) => row.toggleSelected(value)}
        onClick={(event) => {
          // Selection must not bubble into the row's onRowClick handler.
          event.stopPropagation();
        }}
        aria-label="Select row"
      />
    </>
  );
}

function DataGridTableRowSelectAll() {
  const { table, recordCount, isLoading } = useDataGrid();

  const isAllSelected = table.getIsAllPageRowsSelected();
  const isSomeSelected = table.getIsSomePageRowsSelected();

  return (
    <Checkbox
      checked={isAllSelected}
      indeterminate={isSomeSelected && !isAllSelected}
      disabled={isLoading || recordCount === 0}
      onCheckedChange={(value) => table.toggleAllPageRowsSelected(value)}
      aria-label="Select all"
    />
  );
}

function DataGridTableRowExpand<TData>({
  row,
  indent = 20,
  className,
  children,
}: {
  row: Row<TData>;
  /** Horizontal offset in px applied per tree depth level. */
  indent?: number;
  className?: string;
  /** Custom toggle icon; replaces the default chevron. */
  children?: ReactNode;
}) {
  const { props } = useDataGrid<TData>();
  const isExpanded = row.getIsExpanded();
  /* Both steps stay on the ladder: 28px control, 20px badge slot when dense. */
  const controlSize = props.tableLayout?.dense
    ? "size-badge"
    : "size-control-sm";

  return (
    <span
      data-slot="data-grid-table-row-expand"
      style={getDataGridTreeIndentStyle(row, indent)}
      className={cn(
        "inline-flex shrink-0 items-center align-middle ps-(--data-grid-tree-padding)",
        className
      )}
    >
      {row.getCanExpand() ? (
        <button
          type="button"
          aria-expanded={isExpanded}
          aria-label={isExpanded ? "Collapse row" : "Expand row"}
          onClick={(event) => {
            // Expansion must not bubble into the row's onRowClick handler.
            event.stopPropagation();
            row.toggleExpanded();
          }}
          className={cn(
            "inline-flex items-center justify-center rounded-control text-ink-subtle transition-[scale,color] duration-fast ease-out-quint hover:text-ink active:scale-[0.97] motion-reduce:transition-none",
            controlSize
          )}
        >
          {children ?? (
            <ChevronDownGlyph className="transition-transform duration-fast ease-out-quint in-aria-[expanded=false]:-rotate-90 motion-reduce:transition-none rtl:in-aria-[expanded=false]:rotate-90" />
          )}
        </button>
      ) : (
        // Leaf spacer: compact by design so leaf content sits near the
        // parent label instead of a full toggle width deeper.
        <span aria-hidden="true" className="w-2 shrink-0" />
      )}
    </span>
  );
}

function DataGridTableBodyRows<TData>({ table }: { table: Table<TData> }) {
  const { isLoading, props } = useDataGrid<TData>();
  const pagination = table.getState().pagination;

  if (isLoading && props.loadingMode === "skeleton") {
    const leftVisibleColumns = table.getLeftVisibleLeafColumns();
    const centerVisibleColumns = table.getCenterVisibleLeafColumns();
    const rightVisibleColumns = table.getRightVisibleLeafColumns();
    const hasRightPinnedColumns = hasDataGridTableRightPinnedColumns(table);

    return (
      <>
        {Array.from({ length: skeletonRowCount(pagination?.pageSize) }).map((_, rowIndex) => (
          <DataGridTableBodyRowSkeleton key={rowIndex}>
            {[...leftVisibleColumns, ...centerVisibleColumns].map((column) => (
              <DataGridTableBodyRowSkeletonCell column={column} key={column.id}>
                {column.columnDef.meta?.skeleton}
              </DataGridTableBodyRowSkeletonCell>
            ))}
            {props.tableLayout?.columnsResizable && hasRightPinnedColumns ? (
              <DataGridTableFillBodyCell />
            ) : null}
            {rightVisibleColumns.map((column) => (
              <DataGridTableBodyRowSkeletonCell column={column} key={column.id}>
                {column.columnDef.meta?.skeleton}
              </DataGridTableBodyRowSkeletonCell>
            ))}
            {props.tableLayout?.columnsResizable && !hasRightPinnedColumns ? (
              <DataGridTableFillBodyCell />
            ) : null}
          </DataGridTableBodyRowSkeleton>
        ))}
      </>
    );
  }

  if (isLoading && props.loadingMode === "spinner") {
    return (
      <tr>
        <td
          colSpan={
            table.getVisibleFlatColumns().length +
            (props.tableLayout?.columnsResizable ? 1 : 0)
          }
          className="p-8"
        >
          <div className="flex items-center justify-center gap-2 text-body text-ink-subtle">
            <Spinner size="lg" />
            {props.loadingMessage || "Loading..."}
          </div>
        </td>
      </tr>
    );
  }

  const resolvedRows = getDataGridTableResolvedRows(
    table,
    props.tableLayout?.rowsPinnable
  );

  if (!resolvedRows.length) return <DataGridTableEmpty />;

  return (
    <>
      {resolvedRows.map(({ row, pinnedBoundary }) => (
        <DataGridTableRenderedRow
          key={row.id}
          row={row}
          pinnedBoundary={pinnedBoundary}
        />
      ))}
    </>
  );
}

/**
 * Memoized body rows: skip re-renders during active column resize.
 * Column widths update via CSS variables on the <table> element,
 * so the browser handles width changes without React re-renders.
 */
const MemoizedDataGridTableBodyRows = memo(
  DataGridTableBodyRows,
  (_prev, next) => !!next.table.getState().columnSizingInfo.isResizingColumn
) as typeof DataGridTableBodyRows;

function DataGridTableHeaderCells({ headerGroupId }: { headerGroupId: string }) {
  const { table, props } = useDataGrid();
  const mergedHeaderGroups = getDataGridTableMergedHeaderGroups(table);
  const headerGroup = mergedHeaderGroups.find(
    (group) => group.id === headerGroupId
  );
  const hasRightPinnedColumns = hasDataGridTableRightPinnedColumns(table);

  if (!headerGroup) return null;

  const renderCells = (pinnedRight: boolean) =>
    headerGroup.headers
      .filter((header) =>
        pinnedRight
          ? header.column.getIsPinned() === "right"
          : header.column.getIsPinned() !== "right"
      )
      .map((header) => (
        <DataGridTableHeadRowCell header={header} key={header.id}>
          {header.isPlaceholder
            ? null
            : flexRender(header.column.columnDef.header, header.getContext())}
          {props.tableLayout?.columnsResizable &&
          header.column.getCanResize() ? (
            <DataGridTableHeadRowCellResize header={header} />
          ) : null}
        </DataGridTableHeadRowCell>
      ));

  return (
    <>
      {renderCells(false)}
      {props.tableLayout?.columnsResizable && hasRightPinnedColumns ? (
        <DataGridTableFillHeadCell />
      ) : null}
      {renderCells(true)}
      {props.tableLayout?.columnsResizable && !hasRightPinnedColumns ? (
        <DataGridTableFillHeadCell />
      ) : null}
    </>
  );
}

function DataGridTableHeadRows() {
  const { table } = useDataGrid();

  return (
    <DataGridTableHead>
      {getDataGridTableMergedHeaderGroups(table).map((headerGroup) => (
        <DataGridTableHeadRow key={headerGroup.id} rowId={headerGroup.id}>
          <DataGridTableHeaderCells headerGroupId={headerGroup.id} />
        </DataGridTableHeadRow>
      ))}
    </DataGridTableHead>
  );
}

function DataGridTableHeader() {
  return (
    <DataGridTableViewport>
      <DataGridTableBase>
        <DataGridTableHeadRows />
      </DataGridTableBase>
    </DataGridTableViewport>
  );
}

function DataGridTable({
  footerContent,
  renderHeader = true,
}: {
  footerContent?: ReactNode;
  renderHeader?: boolean;
}) {
  const { table, props } = useDataGrid();

  return (
    <DataGridTableViewport>
      <DataGridTableBase>
        {renderHeader ? <DataGridTableHeadRows /> : null}

        {renderHeader &&
        (props.tableLayout?.stripped || !props.tableLayout?.rowBorder) ? (
          <DataGridTableRowSpacer />
        ) : null}

        <DataGridTableBody>
          <MemoizedDataGridTableBodyRows table={table} />
        </DataGridTableBody>

        {footerContent ? (
          <DataGridTableFoot>{footerContent}</DataGridTableFoot>
        ) : null}
      </DataGridTableBase>
    </DataGridTableViewport>
  );
}

function DataGridTableServerGroupRow({
  group,
}: {
  group: DataGridServerGroup;
}) {
  const { props, table } = useDataGrid();
  const leftColumns = table.getLeftVisibleLeafColumns();
  const centerColumns = table.getCenterVisibleLeafColumns();
  const rightColumns = table.getRightVisibleLeafColumns();
  const leadColumn = [...leftColumns, ...centerColumns, ...rightColumns].find(
    (column) => column.id !== SELECT_COLUMN_ID
  );
  const hasRightPinnedColumns = hasDataGridTableRightPinnedColumns(table);
  const bodyRowBottomBorderClasses =
    "[&:not(:last-child)>td]:border-b [&:not(:last-child)>td]:border-line [tbody:has(+tfoot)_&:last-child>td]:border-b [*:has(>[data-slot=data-grid]+[data-slot=data-grid-pagination])_[data-slot=data-grid]_&:last-child>td]:border-b";

  const renderColumn = (column: (typeof leftColumns)[number]) => (
    <DataGridTableBodyRowSkeletonCell column={column} key={column.id}>
      {column.id === leadColumn?.id ? (
        <GroupRowContent
          compact={props.tableLayout?.dense}
          count={group.count}
          expanded={group.expanded}
          label={group.label}
          onToggle={group.onToggle}
        />
      ) : (
        <span aria-hidden />
      )}
    </DataGridTableBodyRowSkeletonCell>
  );

  return (
    <tr
      data-slot="data-grid-server-group"
      data-grouped
      className={cn(
        "group/data-row hover:bg-hover",
        !props.tableLayout?.stripped &&
          props.tableLayout?.rowBorder &&
          bodyRowBottomBorderClasses,
        props.tableLayout?.cellBorder &&
          `*:last:border-e-0 ${bodyRowBottomBorderClasses}`,
        props.tableClassNames?.bodyRow
      )}
    >
      {[...leftColumns, ...centerColumns].map(renderColumn)}
      {props.tableLayout?.columnsResizable && hasRightPinnedColumns ? (
        <DataGridTableFillBodyCell />
      ) : null}
      {rightColumns.map(renderColumn)}
      {props.tableLayout?.columnsResizable && !hasRightPinnedColumns ? (
        <DataGridTableFillBodyCell />
      ) : null}
    </tr>
  );
}

function DataGridTableLoadMoreRow({
  columnCount,
  fetching,
  fetchingLabel,
  hasMore,
  onFetchMore,
}: {
  columnCount: number;
  fetching: boolean;
  fetchingLabel: string;
  hasMore: boolean;
  onFetchMore: () => void;
}) {
  if (!hasMore && !fetching) return null;
  return (
    <tr>
      <td colSpan={columnCount} className="p-0">
        {hasMore && !fetching ? (
          <DataGridLoadMoreSentinel enabled onVisible={onFetchMore} />
        ) : null}
        {fetching ? (
          <Box
            aria-label={fetchingLabel}
            aria-busy
            className="h-row-data w-full"
          >
            <Skeleton className="h-row-data w-full rounded-none" />
          </Box>
        ) : null}
      </td>
    </tr>
  );
}

function DataGridTableServerGrouped({
  grouping,
}: {
  grouping: DataGridServerGrouping;
}) {
  const { isLoading, props, table } = useDataGrid();
  const columnCount =
    table.getVisibleFlatColumns().length +
    (props.tableLayout?.columnsResizable ? 1 : 0);
  const rows = table.getRowModel().rows;

  return (
    <DataGridTableViewport>
      <DataGridTableBase>
        <DataGridTableHeadRows />
        {props.tableLayout?.stripped || !props.tableLayout?.rowBorder ? (
          <DataGridTableRowSpacer />
        ) : null}
        <DataGridTableBody>
          {isLoading ? (
            <MemoizedDataGridTableBodyRows table={table} />
          ) : grouping.groups.length === 0 ? (
            <DataGridTableEmpty />
          ) : (
            <>
              {grouping.groups.map((group) => (
                <Fragment key={group.id}>
                  <DataGridTableServerGroupRow group={group} />
                  {group.expanded && group.isLoading ? (
                    <tr>
                      <td colSpan={columnCount} className="p-3">
                        <Skeleton className="h-3 w-1/3" />
                      </td>
                    </tr>
                  ) : null}
                  {group.expanded && !group.isLoading
                    ? rows.map((row) => (
                        <DataGridTableRenderedRow key={row.id} row={row} />
                      ))
                    : null}
                  {group.expanded && !group.isLoading ? (
                    <DataGridTableLoadMoreRow
                      columnCount={columnCount}
                      fetching={group.isFetchingMore}
                      fetchingLabel="Loading more rows"
                      hasMore={group.hasMore}
                      onFetchMore={group.onFetchMore}
                    />
                  ) : null}
                </Fragment>
              ))}
              <DataGridTableLoadMoreRow
                columnCount={columnCount}
                fetching={grouping.isFetchingMore}
                fetchingLabel="Loading more groups"
                hasMore={grouping.hasMore}
                onFetchMore={grouping.onFetchMore}
              />
            </>
          )}
        </DataGridTableBody>
      </DataGridTableBase>
    </DataGridTableViewport>
  );
}

export {
  DataGridTable,
  DataGridTableServerGrouped,
  DataGridTableBase,
  DataGridTableBody,
  DataGridTableBodyRow,
  DataGridTableBodyRowCell,
  DataGridTableBodyRowExpandded,
  DataGridTableRenderedRow,
  DataGridTableBodyRowSkeleton,
  DataGridTableBodyRowSkeletonCell,
  DataGridTableEmpty,
  DataGridTableFillBodyCell,
  DataGridTableFillHeadCell,
  DataGridTableFoot,
  DataGridTableFootRow,
  DataGridTableFootRowCell,
  DataGridTableHeader,
  DataGridTableHead,
  DataGridTableHeadRow,
  DataGridTableHeadRows,
  DataGridTableHeadRowCell,
  DataGridTableHeadRowCellResize,
  DataGridTableLoader,
  DataGridTableRowExpand,
  DataGridTableRowPin,
  DataGridTableRowSelect,
  DataGridTableRowSelectAll,
  DataGridTableRowSpacer,
  DataGridTableViewport,
  getDataGridScrollAreaViewport,
  getDataGridTableMergedHeaderGroups,
  getPinningStyles,
  getDataGridTableResolvedRows,
  getDataGridTableRowSections,
  getDataGridTreeIndentStyle,
  hasDataGridTableRightPinnedColumns,
};

export type {
  DataGridServerGroup,
  DataGridServerGrouping,
  DataGridTablePinnedBoundary,
};
