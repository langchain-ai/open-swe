"use client";
"use no memo";

/*
 * ReUI `data-grid` column header, vendored (free tier) and retokenized.
 * Source: https://reui.io/r/data-grid.json, 2026-08-04.
 *
 * Two adaptations to this app, both recorded in web/reference/VENDORED.md:
 *   - ReUI's `IconPlaceholder` resolves to the consuming project's icon
 *     library; ours is the local glyph set (see data-grid-glyphs.tsx).
 *   - the menu is our `../ui/dropdown-menu`, which is Base UI's Menu
 *     with the same Root/Trigger/Content/Item anatomy ReUI expects.
 */

import { memo } from "react";
import type { HTMLAttributes, ReactNode } from "react";
import type { Column } from "@tanstack/react-table";

import { cn } from "../ui/cn";
import { Button } from "../ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";
import {
  ArrowDownGlyph,
  ArrowLeftGlyph,
  ArrowLeftToLineGlyph,
  ArrowRightGlyph,
  ArrowRightToLineGlyph,
  ArrowUpGlyph,
  CheckGlyph,
  ChevronsUpDownGlyph,
  ColumnsGlyph,
  PinOffGlyph,
} from "./data-grid-glyphs";
import {
  getColumnHeaderLabel,
  useDataGrid,
} from "./data-grid";

/* The active-state tick that marks the applied sort or pin. */
const MENU_TICK_CLASS = "text-primary";

interface DataGridColumnHeaderProps<TData, TValue>
  extends HTMLAttributes<HTMLDivElement> {
  column: Column<TData, TValue>;
  /** When omitted, uses `column.columnDef.meta.headerTitle`, then a string `columnDef.header`, then `column.id`. */
  title?: string;
  icon?: ReactNode;
  /** Reserved; pin controls are gated by tableLayout.columnsPinnable + column.getCanPin(). */
  pinnable?: boolean;
  filter?: ReactNode;
  visibility?: boolean;
}

function DataGridColumnHeaderInner<TData, TValue>({
  column,
  title,
  icon,
  className,
  filter,
  visibility = false,
}: DataGridColumnHeaderProps<TData, TValue>) {
  const { isLoading, table, props } = useDataGrid<TData>();
  const resolvedTitle = title ?? getColumnHeaderLabel(column);

  // TanStack's columnOrder defaults to [] until a consumer seeds it; fall
  // back to the definition order so Move Left/Right work out of the box.
  const columnOrderState = table.getState().columnOrder;
  const columnOrder =
    columnOrderState.length > 0
      ? columnOrderState
      : table.getAllLeafColumns().map((leafColumn) => leafColumn.id);
  const isSorted = column.getIsSorted();
  const isPinned = column.getIsPinned();
  const canSort = column.getCanSort();
  const canPin = column.getCanPin();
  const canResize = column.getCanResize();

  const columnIndex = columnOrder.indexOf(column.id);
  const canMoveLeft = columnIndex > 0;
  const canMoveRight = columnIndex < columnOrder.length - 1;

  const handleSort = () => {
    if (isSorted === "asc") {
      column.toggleSorting(true);
    } else if (isSorted === "desc") {
      column.clearSorting();
    } else {
      column.toggleSorting(false);
    }
  };

  const headerLabelClassName = cn(
    "inline-flex h-full min-w-0 items-center gap-2.5 text-label font-normal text-ink-muted [&_svg]:size-3.5 [&_svg]:shrink-0 [&_svg]:opacity-60",
    className
  );

  const headerButtonClassName = cn(
    "min-w-0 max-w-full px-3 font-normal text-ink-muted hover:bg-hover hover:text-ink data-[state=open]:bg-hover data-[state=open]:text-ink",
    className
  );

  const headerTitle = (
    <span className="min-w-0 truncate">{resolvedTitle}</span>
  );

  const sortIcon =
    canSort &&
    (isSorted === "desc" ? (
      <ArrowDownGlyph />
    ) : isSorted === "asc" ? (
      <ArrowUpGlyph />
    ) : (
      <ChevronsUpDownGlyph className="mt-px" />
    ));

  const hasControls =
    props.tableLayout?.columnsMovable ||
    (props.tableLayout?.columnsVisibility && visibility) ||
    (props.tableLayout?.columnsPinnable && canPin) ||
    filter;

  /*
   * DEVIATION from ReUI: upstream memoizes this list and silences
   * `react-hooks/exhaustive-deps` for the `JSON.stringify(columnVisibility)`
   * entry that keeps the checkbox states fresh. Building a dozen menu nodes is
   * cheaper than the bug that dependency list was hiding, and the component
   * itself is still `memo`-wrapped below.
   */
  const buildMenuItems = () => {
    const items: ReactNode[] = [];
    let hasPreviousSection = false;

    if (filter) {
      items.push(
        <DropdownMenuGroup key="group-filter">
          <DropdownMenuLabel key="filter">{filter}</DropdownMenuLabel>
        </DropdownMenuGroup>
      );
      hasPreviousSection = true;
    }

    if (canSort) {
      if (hasPreviousSection) {
        items.push(<DropdownMenuSeparator key="sep-sort" />);
      }
      items.push(
        <DropdownMenuItem
          key="sort-asc"
          onClick={() => {
            if (isSorted === "asc") {
              column.clearSorting();
            } else {
              column.toggleSorting(false);
            }
          }}
          disabled={!canSort}
        >
          <ArrowUpGlyph />
          <span className="grow">Asc</span>
          {isSorted === "asc" ? <CheckGlyph className={MENU_TICK_CLASS} /> : null}
        </DropdownMenuItem>,
        <DropdownMenuItem
          key="sort-desc"
          onClick={() => {
            if (isSorted === "desc") {
              column.clearSorting();
            } else {
              column.toggleSorting(true);
            }
          }}
          disabled={!canSort}
        >
          <ArrowDownGlyph />
          <span className="grow">Desc</span>
          {isSorted === "desc" ? (
            <CheckGlyph className={MENU_TICK_CLASS} />
          ) : null}
        </DropdownMenuItem>
      );
      hasPreviousSection = true;
    }

    if (props.tableLayout?.columnsPinnable && canPin) {
      if (hasPreviousSection) {
        items.push(<DropdownMenuSeparator key="sep-pin" />);
      }
      items.push(
        <DropdownMenuItem
          key="pin-left"
          onClick={() => column.pin(isPinned === "left" ? false : "left")}
        >
          <ArrowLeftToLineGlyph />
          <span className="grow">Pin to left</span>
          {isPinned === "left" ? (
            <CheckGlyph className={MENU_TICK_CLASS} />
          ) : null}
        </DropdownMenuItem>,
        <DropdownMenuItem
          key="pin-right"
          onClick={() => column.pin(isPinned === "right" ? false : "right")}
        >
          <ArrowRightToLineGlyph />
          <span className="grow">Pin to right</span>
          {isPinned === "right" ? (
            <CheckGlyph className={MENU_TICK_CLASS} />
          ) : null}
        </DropdownMenuItem>
      );
      hasPreviousSection = true;
    }

    if (props.tableLayout?.columnsMovable) {
      if (hasPreviousSection) {
        items.push(<DropdownMenuSeparator key="sep-move" />);
      }
      items.push(
        <DropdownMenuItem
          key="move-left"
          onClick={() => {
            if (columnIndex > 0) {
              const newOrder = [...columnOrder];
              const [movedColumn] = newOrder.splice(columnIndex, 1);
              if (movedColumn !== undefined) {
                newOrder.splice(columnIndex - 1, 0, movedColumn);
                table.setColumnOrder(newOrder);
              }
            }
          }}
          disabled={!canMoveLeft || isPinned !== false}
        >
          <ArrowLeftGlyph />
          <span>Move to Left</span>
        </DropdownMenuItem>,
        <DropdownMenuItem
          key="move-right"
          onClick={() => {
            if (columnIndex < columnOrder.length - 1) {
              const newOrder = [...columnOrder];
              const [movedColumn] = newOrder.splice(columnIndex, 1);
              if (movedColumn !== undefined) {
                newOrder.splice(columnIndex + 1, 0, movedColumn);
                table.setColumnOrder(newOrder);
              }
            }
          }}
          disabled={!canMoveRight || isPinned !== false}
        >
          <ArrowRightGlyph />
          <span>Move to Right</span>
        </DropdownMenuItem>
      );
      hasPreviousSection = true;
    }

    if (props.tableLayout?.columnsVisibility && visibility) {
      if (hasPreviousSection) {
        items.push(<DropdownMenuSeparator key="sep-visibility" />);
      }
      items.push(
        <DropdownMenuSub key="visibility">
          <DropdownMenuSubTrigger>
            <ColumnsGlyph />
            <span>Columns</span>
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent side="right">
            {table
              .getAllColumns()
              .filter((col) => col.getCanHide())
              .map((col) => (
                <DropdownMenuCheckboxItem
                  key={col.id}
                  checked={col.getIsVisible()}
                  onSelect={(event) => event.preventDefault()}
                  onCheckedChange={(value) => col.toggleVisibility(!!value)}
                  className="capitalize"
                >
                  {getColumnHeaderLabel(col)}
                </DropdownMenuCheckboxItem>
              ))}
          </DropdownMenuSubContent>
        </DropdownMenuSub>
      );
    }

    return items;
  };

  if (hasControls) {
    return (
      <div className="-ms-1 flex h-full min-w-0 items-center justify-between gap-2">
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button
                variant="ghost"
                size="compact"
                className={headerButtonClassName}
                disabled={isLoading}
                title={resolvedTitle}
              >
                {icon}
                {headerTitle}
                {sortIcon}
              </Button>
            }
          />
          <DropdownMenuContent className="w-40" align="start">
            {buildMenuItems()}
          </DropdownMenuContent>
        </DropdownMenu>
        {props.tableLayout?.columnsPinnable && canPin && isPinned ? (
          <Button
            size="icon-sm"
            variant="ghost"
            className="-me-1"
            onClick={() => column.pin(false)}
            aria-label={`Unpin ${resolvedTitle} column`}
            title={`Unpin ${resolvedTitle} column`}
          >
            <PinOffGlyph className="opacity-50" />
          </Button>
        ) : null}
      </div>
    );
  }

  if (canSort || (props.tableLayout?.columnsResizable && canResize)) {
    return (
      <div className="-ms-1 flex h-full min-w-0 items-center">
        <Button
          variant="ghost"
          size="compact"
          className={headerButtonClassName}
          disabled={isLoading}
          title={resolvedTitle}
          onClick={handleSort}
        >
          {icon}
          {headerTitle}
          {sortIcon}
        </Button>
      </div>
    );
  }

  return (
    <div className={headerLabelClassName} title={resolvedTitle}>
      {icon}
      {headerTitle}
    </div>
  );
}

const DataGridColumnHeader = memo(
  DataGridColumnHeaderInner
) as typeof DataGridColumnHeaderInner;

export { DataGridColumnHeader, type DataGridColumnHeaderProps };
