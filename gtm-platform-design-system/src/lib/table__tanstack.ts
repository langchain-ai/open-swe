/*
 * The one import boundary to TanStack Table and TanStack Virtual.
 *
 * Wiki 01 principle 13 asks for a single seam so the Table v9 migration touches
 * one file. `use-app-table.ts` is the *policy* layer (defaults, row-height
 * contract, getRowId); this is the *binding* layer: the only module in the app
 * that names `@tanstack/react-table` or `@tanstack/react-virtual` as an import
 * source for their hooks. Repointing the app at Table v9 or Virtual v4 starts
 * here.
 *
 * It also settles a lint question, and this is worth reading before deleting
 * the file. `react-hooks/incompatible-library` warns at every direct
 * `useReactTable` / `useVirtualizer` call site: both hooks return functions the
 * React Compiler cannot memoize, so the compiler skips compiling the calling
 * component. That is a fact about the libraries, not a defect at the call site,
 * and it has exactly one correct response -- do not let the compiler memoize
 * those components -- which this suite already takes: `DataGridTableVirtual`
 * carries `"use no memo"` at file and function scope, the data-grid modules
 * carry it at file scope, and the one memoized consumer downstream
 * (`MemoizedVirtualBody`) skips re-rendering only while a column resize drag is
 * live. Stating that once, here, is the point of the seam; restating it as a
 * suppressed warning in every grid is not.
 *
 * If the React Compiler is enabled repo-wide later, re-audit this: the warning
 * is informational only while the opt-outs above hold.
 */

export {
  flexRender,
  getCoreRowModel,
  getExpandedRowModel,
  getFacetedRowModel,
  getFacetedUniqueValues,
  getFilteredRowModel,
  getGroupedRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";

export { useVirtualizer } from "@tanstack/react-virtual";

export type {
  VirtualItem,
  Virtualizer,
  VirtualizerOptions,
} from "@tanstack/react-virtual";
