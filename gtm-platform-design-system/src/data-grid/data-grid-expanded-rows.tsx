"use client";

/*
 * Expand-as-rows paint for FilterableTable. Not vendored: the ReUI grid only
 * ships a colspan detail slot. Child rows reuse parent leaf columns for pin
 * and width. They are not in the TanStack row model and are not virtualized.
 */

import type { ReactNode } from "react";

import { cn } from "../ui/cn";
import { useDataGrid } from "./data-grid";
import {
  DataGridTableBodyRowCell,
  DataGridTableFillBodyCell,
  hasDataGridTableRightPinnedColumns,
} from "./data-grid-table";

const BODY_ROW_BOTTOM_BORDER_CLASSES =
  "[&:not(:last-child)>td]:border-b [&:not(:last-child)>td]:border-line [tbody:has(+tfoot)_&:last-child>td]:border-b [*:has(>[data-slot=data-grid]+[data-slot=data-grid-pagination])_[data-slot=data-grid]_&:last-child>td]:border-b";

function ExpandedRow<TData>({
  cells,
  id,
  kind,
  slot,
}: {
  cells: Readonly<Record<string, ReactNode>>;
  id: string;
  kind?: string;
  slot?: string;
}) {
  const { props, table } = useDataGrid<TData>();
  const leftVisibleColumns = table.getLeftVisibleLeafColumns();
  const centerVisibleColumns = table.getCenterVisibleLeafColumns();
  const rightVisibleColumns = table.getRightVisibleLeafColumns();
  const hasRightPinnedColumns = hasDataGridTableRightPinnedColumns(table);

  return (
    <tr
      data-slot={slot ?? "data-grid-expanded-row"}
      data-tree-kind={kind}
      className={cn(
        "group/data-row hover:bg-hover",
        !props.tableLayout?.stripped &&
          props.tableLayout?.rowBorder &&
          BODY_ROW_BOTTOM_BORDER_CLASSES,
        props.tableLayout?.cellBorder &&
          `*:last:border-e-0 ${BODY_ROW_BOTTOM_BORDER_CLASSES}`
      )}
    >
      {[...leftVisibleColumns, ...centerVisibleColumns].map((column) => (
        <DataGridTableBodyRowCell column={column} key={`${id}:${column.id}`}>
          {cells[column.id]}
        </DataGridTableBodyRowCell>
      ))}
      {props.tableLayout?.columnsResizable && hasRightPinnedColumns ? (
        <DataGridTableFillBodyCell />
      ) : null}
      {rightVisibleColumns.map((column) => (
        <DataGridTableBodyRowCell column={column} key={`${id}:${column.id}`}>
          {cells[column.id]}
        </DataGridTableBodyRowCell>
      ))}
      {props.tableLayout?.columnsResizable && !hasRightPinnedColumns ? (
        <DataGridTableFillBodyCell />
      ) : null}
    </tr>
  );
}

export { ExpandedRow };
