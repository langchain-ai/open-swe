"use client";
"use no memo";

/*
 * ReUI `data-grid` pagination, vendored (free tier) and retokenized.
 * Source: https://reui.io/r/data-grid.json, 2026-08-04.
 *
 * The page-size picker is `../ui/select`, which is what upstream
 * resolves too, so the composition here is ReUI's: string values through
 * `value` / `onValueChange`, `Number()` on the way back into
 * `table.setPageSize`. It replaces the dropdown-menu radio group this file
 * carried while the app had no select primitive; `DataGridPaginationProps` is
 * unchanged, so nothing above it moved.
 *
 * The pagination strip is a 28px row: the page buttons are `icon-sm` and the
 * page-size trigger is `size="compact"`, which is Select's own variant rather
 * than a call-site height override.
 */

import type { JSX, ReactNode } from "react";

import { cn } from "../ui/cn";
import { Button } from "../ui/button";
import { Skeleton } from "../ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../ui/select";
import {
  ChevronLeftGlyph,
  ChevronRightGlyph,
} from "./data-grid-glyphs";
import { useDataGrid } from "./data-grid";
import { PAGE_SIZES } from "../lib/table__use-app-table";

interface DataGridPaginationProps {
  sizes?: number[];
  sizesInfo?: string;
  sizesLabel?: string;
  sizesDescription?: string;
  sizesSkeleton?: ReactNode;
  more?: boolean;
  moreLimit?: number;
  info?: string;
  infoSkeleton?: ReactNode;
  className?: string;
  rowsPerPageLabel?: string;
  previousPageLabel?: string;
  nextPageLabel?: string;
  ellipsisText?: string;
  /** Hide the page-size picker (cursor-paged lists: the server owns the size). */
  showSizes?: boolean;
  /**
   * Hide 1 2 3 when the total is unknown. Prev/next still render when
   * `canPreviousPage` / `canNextPage` say so.
   */
  showPageNumbers?: boolean;
  canPreviousPage?: boolean;
  canNextPage?: boolean;
  onPreviousPage?: () => void;
  onNextPage?: () => void;
}

function DataGridPagination(props: DataGridPaginationProps): JSX.Element {
  const { table, recordCount, isLoading } = useDataGrid();

  const defaultProps: Partial<DataGridPaginationProps> = {
    sizes: [...PAGE_SIZES],
    sizesSkeleton: <Skeleton className="h-control-sm w-44" />,
    moreLimit: 5,
    info: "{from} - {to} of {count}",
    infoSkeleton: <Skeleton className="h-control-sm w-60" />,
    rowsPerPageLabel: "Rows per page",
    previousPageLabel: "Go to previous page",
    nextPageLabel: "Go to next page",
    ellipsisText: "...",
  };

  const mergedProps: DataGridPaginationProps = { ...defaultProps, ...props };

  const pageIndex = table.getState().pagination.pageIndex;
  const pageSize = table.getState().pagination.pageSize;
  const from = recordCount === 0 ? 0 : pageIndex * pageSize + 1;
  const to = Math.min((pageIndex + 1) * pageSize, recordCount);
  const pageCount = table.getPageCount();
  const showSizes = mergedProps.showSizes ?? true;
  const showPageNumbers = mergedProps.showPageNumbers ?? pageCount > 1;
  const canPreviousPage =
    mergedProps.canPreviousPage ?? table.getCanPreviousPage();
  const canNextPage = mergedProps.canNextPage ?? table.getCanNextPage();
  const showPager = showPageNumbers || canPreviousPage || canNextPage;

  const goPrevious = () => {
    if (mergedProps.onPreviousPage !== undefined) {
      mergedProps.onPreviousPage();
      return;
    }
    table.previousPage();
  };

  const goNext = () => {
    if (mergedProps.onNextPage !== undefined) {
      mergedProps.onNextPage();
      return;
    }
    table.nextPage();
  };

  // Replace placeholders in paginationInfo
  const paginationInfo = mergedProps.info
    ? mergedProps.info
        .replaceAll("{from}", from.toString())
        .replaceAll("{to}", to.toString())
        .replaceAll("{count}", recordCount.toString())
    : `${from} - ${to} of ${recordCount}`;

  // Pagination limit logic
  const paginationMoreLimit = mergedProps.moreLimit || 5;

  // Determine the start and end of the pagination group
  const currentGroupStart =
    Math.floor(pageIndex / paginationMoreLimit) * paginationMoreLimit;
  const currentGroupEnd = Math.min(
    currentGroupStart + paginationMoreLimit,
    pageCount
  );

  // Render page buttons based on the current group
  const renderPageButtons = () => {
    const buttons = [];
    for (let i = currentGroupStart; i < currentGroupEnd; i++) {
      buttons.push(
        <Button
          key={i}
          size="icon-sm"
          variant="ghost"
          className={cn(
            "p-0 text-label text-ink-subtle",
            pageIndex === i && "bg-selected text-ink"
          )}
          onClick={() => {
            if (pageIndex !== i) {
              table.setPageIndex(i);
            }
          }}
        >
          {i + 1}
        </Button>
      );
    }
    return buttons;
  };

  // Render a "previous" ellipsis button if there are previous pages to show
  const renderEllipsisPrevButton = () => {
    if (currentGroupStart > 0) {
      return (
        <Button
          size="icon-sm"
          className="p-0 text-label"
          variant="ghost"
          onClick={() => table.setPageIndex(currentGroupStart - 1)}
        >
          {mergedProps.ellipsisText}
        </Button>
      );
    }
    return null;
  };

  // Render a "next" ellipsis button if there are more pages to show after the current group
  const renderEllipsisNextButton = () => {
    if (currentGroupEnd < pageCount) {
      return (
        <Button
          className="p-0 text-label"
          variant="ghost"
          size="icon-sm"
          onClick={() => table.setPageIndex(currentGroupEnd)}
        >
          {mergedProps.ellipsisText}
        </Button>
      );
    }
    return null;
  };

  return (
    <div
      data-slot="data-grid-pagination"
      className={cn(
        "flex w-full shrink-0 flex-col flex-wrap items-center justify-between gap-2.5 py-2.5 sm:flex-row sm:py-0",
        mergedProps.className
      )}
    >
      <div className="order-2 flex flex-wrap items-center gap-2.5 pb-2.5 sm:order-1 sm:pb-0">
        {isLoading ? (
          mergedProps.sizesSkeleton
        ) : showSizes ? (
          <>
            <div className="text-label text-ink-subtle">
              {mergedProps.rowsPerPageLabel}
            </div>
            <Select
              value={`${pageSize}`}
              onValueChange={(value) => {
                /*
                 * Base UI hands back `null` when a selection is cleared, which
                 * `Number()` would silently turn into page size 0. There is no
                 * clear affordance on this control, so the guard is cheap
                 * insurance rather than a live path.
                 */
                if (value !== null) {
                  table.setPageSize(Number(value));
                }
              }}
            >
              <SelectTrigger
                size="compact"
                className="w-16"
                aria-label={mergedProps.rowsPerPageLabel}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent align="start">
                {mergedProps.sizes?.map((size: number) => (
                  <SelectItem key={size} value={`${size}`}>
                    {size}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </>
        ) : null}
      </div>
      <div className="order-1 flex flex-col items-center justify-center gap-2.5 pt-2.5 sm:order-2 sm:flex-row sm:justify-end sm:pt-0">
        {isLoading ? (
          mergedProps.infoSkeleton
        ) : (
          <>
            <div className="order-2 text-label text-nowrap text-ink-subtle sm:order-1">
              {paginationInfo}
            </div>
            {showPager ? (
              <div className="order-1 flex items-center gap-1">
                <Button
                  size="icon-sm"
                  variant="ghost"
                  className="p-0 text-label rtl:rotate-180"
                  onClick={goPrevious}
                  disabled={!canPreviousPage}
                >
                  <span className="sr-only">
                    {mergedProps.previousPageLabel}
                  </span>
                  <ChevronLeftGlyph />
                </Button>

                {showPageNumbers ? renderEllipsisPrevButton() : null}

                {showPageNumbers ? renderPageButtons() : null}

                {showPageNumbers ? renderEllipsisNextButton() : null}

                <Button
                  size="icon-sm"
                  variant="ghost"
                  className="p-0 text-label rtl:rotate-180"
                  onClick={goNext}
                  disabled={!canNextPage}
                >
                  <span className="sr-only">{mergedProps.nextPageLabel}</span>
                  <ChevronRightGlyph />
                </Button>
              </div>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

export { DataGridPagination, type DataGridPaginationProps };
