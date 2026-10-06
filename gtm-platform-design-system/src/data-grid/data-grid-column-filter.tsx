"use client";
"use no memo";

/*
 * ReUI `data-grid` column filter, vendored (free tier, no licence key) and
 * retokenized. Source: https://reui.io/r/data-grid.json, 2026-08-05.
 *
 * The faceted multi-select a column header hangs off its `filter` prop: a
 * searchable option list over `column.getFacetedUniqueValues()`, writing the
 * selection back as `column.setFilterValue(string[] | undefined)`.
 *
 * Adaptations to this app, all recorded in web/reference/VENDORED.md:
 *   - ReUI's `IconPlaceholder` resolves to the consuming project's icon
 *     library; ours is the local glyph set (see data-grid-glyphs.tsx).
 *   - popover, input, badge and separator are `@/components/ui/*`, which is
 *     the same Base UI anatomy ReUI expects.
 *   - the tick square borrows `../ui/checkbox`'s palette exactly
 *     (16px, badge radius, `line-strong` + `muted` at rest, the primary pair
 *     when set) so the two read as the same control. It stays a painted square
 *     rather than a real Checkbox because the whole option row is the pressable
 *     and a checkbox nested inside a `role="button"` is two controls where the
 *     user sees one.
 */

import { useState } from "react";
import type { ComponentType } from "react";
import type { Column } from "@tanstack/react-table";

import { cn } from "../ui/cn";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "../ui/popover";
import { ScrollArea } from "../ui/scroll-area";
import { Separator } from "../ui/separator";
import {
  CheckGlyph,
  CirclePlusGlyph,
} from "./data-grid-glyphs";

/* The option row, on the menu-item step: compact radius inside a panel popup. */
const FILTER_OPTION_CLASS =
  "relative flex cursor-pointer items-center gap-1.5 rounded-compact px-1.5 py-1 text-label outline-hidden select-none hover:bg-hover hover:text-ink focus:bg-hover focus:text-ink";

interface DataGridColumnFilterOption {
  label: string;
  value: string;
  /** Rendered before the label; sized by the caller through `className`. */
  icon?: ComponentType<{ className?: string }>;
}

interface DataGridColumnFilterProps<TData, TValue> {
  column?: Column<TData, TValue>;
  title?: string;
  options: DataGridColumnFilterOption[];
}

function DataGridColumnFilter<TData, TValue>({
  column,
  title,
  options,
}: DataGridColumnFilterProps<TData, TValue>) {
  const [searchQuery, setSearchQuery] = useState("");

  const facets = column?.getFacetedUniqueValues();
  const filterValue = column?.getFilterValue();
  const selectedValues = new Set(
    Array.isArray(filterValue) ? (filterValue as string[]) : []
  );

  /*
   * DEVIATION from ReUI: upstream memoizes this filter on
   * `[options, searchQuery]`. Filtering a facet list is one pass over an array
   * the popover is already about to render, and wiki 01 principle 8 asks for
   * manual memoization only at profiler-proven hot spots, so it is computed
   * plain like the rest of this suite.
   */
  const normalizedQuery = searchQuery.trim().toLowerCase();
  const filteredOptions = normalizedQuery
    ? options.filter((option) =>
        option.label.toLowerCase().includes(normalizedQuery)
      )
    : options;

  /*
   * DEVIATION from ReUI: upstream mutates the `selectedValues` set built during
   * render, which leaves every other option row in the same pass reading a
   * selection state that no longer matches what it painted. The next values are
   * derived from a copy instead; the write to `setFilterValue` is identical.
   */
  const toggleOption = (value: string) => {
    const next = new Set(selectedValues);
    if (next.has(value)) {
      next.delete(value);
    } else {
      next.add(value);
    }
    const filterValues = Array.from(next);
    column?.setFilterValue(filterValues.length > 0 ? filterValues : undefined);
  };

  const clearFilter = () => column?.setFilterValue(undefined);

  return (
    <Popover>
      <PopoverTrigger
        render={
          <Button variant="outline">
            <CirclePlusGlyph />
            {title}
            {selectedValues.size > 0 ? (
              <>
                {/*
                 * `render` keeps this a span: the trigger is a real <button>,
                 * whose content model is phrasing content only.
                 */}
                <Separator
                  orientation="vertical"
                  render={<span />}
                  className="mx-1 block h-4"
                />
                <Badge className="lg:hidden">{selectedValues.size}</Badge>
                <span className="hidden gap-1 lg:flex">
                  {selectedValues.size > 2 ? (
                    <Badge>{selectedValues.size} selected</Badge>
                  ) : (
                    options
                      .filter((option) => selectedValues.has(option.value))
                      .map((option) => (
                        <Badge key={option.value}>{option.label}</Badge>
                      ))
                  )}
                </span>
              </>
            ) : null}
          </Button>
        }
      />
      <PopoverContent align="start" className="w-50 p-0">
        <div className="p-2">
          <Input
            placeholder={title}
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
          />
        </div>
        {/*
         * The facet list is a popup body, not a grid: the table exception to
         * the scrollbar law does not reach it, so it takes the overlay thumb
         * like every other scroll region in the product.
         */}
        <ScrollArea overflow="vertical" viewportClassName="max-h-72">
          <div className="p-1">
            {filteredOptions.length === 0 ? (
              <div className="py-6 text-center text-label text-ink-subtle">
                No results found.
              </div>
            ) : (
              filteredOptions.map((option) => {
                const isSelected = selectedValues.has(option.value);
                const facetCount = facets?.get(option.value);
                return (
                  <div
                    key={option.value}
                    role="button"
                    tabIndex={0}
                    aria-pressed={isSelected}
                    onClick={() => toggleOption(option.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        toggleOption(option.value);
                      }
                    }}
                    className={FILTER_OPTION_CLASS}
                  >
                    <div
                      className={cn(
                        "flex size-4 shrink-0 items-center justify-center rounded-tick border transition-colors duration-fast ease-out-quint motion-reduce:transition-none",
                        isSelected
                          ? "border-primary bg-primary text-primary-ink"
                          : "border-line-strong bg-muted [&_svg]:invisible"
                      )}
                    >
                      <CheckGlyph />
                    </div>
                    {option.icon ? (
                      <option.icon className="size-3.5 shrink-0 text-ink-subtle" />
                    ) : null}
                    <span className="truncate">{option.label}</span>
                    {facetCount === undefined ? null : (
                      <span className="ms-auto ps-1.5 font-mono text-meta text-ink-subtle">
                        {facetCount}
                      </span>
                    )}
                  </div>
                );
              })
            )}
            {selectedValues.size > 0 ? (
              <>
                <Separator className="-mx-1 my-1" />
                <div
                  role="button"
                  tabIndex={0}
                  onClick={clearFilter}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      clearFilter();
                    }
                  }}
                  className={cn(FILTER_OPTION_CLASS, "justify-center")}
                >
                  Clear filters
                </div>
              </>
            ) : null}
          </div>
        </ScrollArea>
      </PopoverContent>
    </Popover>
  );
}

export {
  DataGridColumnFilter,
  type DataGridColumnFilterOption,
  type DataGridColumnFilterProps,
};
