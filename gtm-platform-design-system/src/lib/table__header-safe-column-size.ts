/*
 * Default column geometry for every useAppTable grid.
 *
 * FilterableTable headers always paint a glyph, a title, and a sort control
 * inside a compact menu button. A caller `size` of 76 or the old 120 floor
 * still crops "Plays". Size each column from its title so first paint shows
 * the full header; the pane scrolls sideways when the sum does not fit.
 */

import type { ColumnDef } from "@tanstack/react-table";

/** Floor when a title is empty or shorter than the chrome itself. */
export const DEFAULT_COLUMN_MIN_SIZE = 120;

export const DEFAULT_COLUMN_MAX_SIZE = 560;

/**
 * Pixels around the title in a SchemaHeader / DataGridColumnHeader:
 * cell `px-4` (32), `-ms-1` (−4), button `px-3` (24), `gap-1.5` × 2 (12),
 * header glyph (14), sort glyph (14), last-column `pe-8` extra (16), and
 * an 8px subpixel/font buffer.
 */
export const HEADER_CHROME_PX = 116;

/** Conservative 13px Inter `text-label` advance. Wide enough that "Plays" fits. */
export const HEADER_LABEL_CHAR_PX = 8;

const SELECT_COLUMN_ID = "select";

function columnHeaderTitle<TData>(
  column: ColumnDef<TData, unknown>
): string {
  const meta = column.meta as { headerTitle?: string } | undefined;
  if (typeof meta?.headerTitle === "string" && meta.headerTitle.length > 0) {
    return meta.headerTitle;
  }
  if (typeof column.header === "string") return column.header;
  if (typeof column.id === "string" && column.id.length > 0) return column.id;
  if ("accessorKey" in column && column.accessorKey != null) {
    return String(column.accessorKey);
  }
  return "";
}

function headerLabelWidth(title: string): number {
  return title.length * HEADER_LABEL_CHAR_PX;
}

/** Minimum painted width so this column's header title is not ellipsized. */
export function headerSafeMinSize<TData>(
  column: ColumnDef<TData, unknown>
): number {
  if (column.id === SELECT_COLUMN_ID || column.enableResizing === false) {
    return column.size ?? column.minSize ?? DEFAULT_COLUMN_MIN_SIZE;
  }
  return Math.max(
    DEFAULT_COLUMN_MIN_SIZE,
    HEADER_CHROME_PX + headerLabelWidth(columnHeaderTitle(column))
  );
}

/**
 * Lift `size` / `minSize` so a caller cannot open a column narrower than its
 * header. Explicit wider sizes still win. The select column is left alone.
 */
export function withHeaderSafeColumnSizing<TData>(
  columns: ColumnDef<TData, unknown>[]
): ColumnDef<TData, unknown>[] {
  return columns.map((column) => {
    if (column.id === SELECT_COLUMN_ID || column.enableResizing === false) {
      return column;
    }
    const minSize = Math.max(column.minSize ?? 0, headerSafeMinSize(column));
    const size = Math.max(column.size ?? minSize, minSize);
    const maxSize = Math.max(column.maxSize ?? DEFAULT_COLUMN_MAX_SIZE, minSize);
    if (
      column.minSize === minSize &&
      column.size === size &&
      (column.maxSize ?? DEFAULT_COLUMN_MAX_SIZE) === maxSize
    ) {
      return column;
    }
    return { ...column, minSize, size, maxSize };
  });
}
