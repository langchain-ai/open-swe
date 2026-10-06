"use client";

/*
 * Rules for TableFilters.
 *
 * The rules themselves are `TABLE_FILTERS_RULES` below, not this comment.
 * `/design` renders FilterableTable's rules; this file is the Filters popover
 * that composition owns. Copy the ReUI Filters command shape (c-filters-3
 * custom trigger, hierarchical category drill-in), re-expressed in CORE 14.
 */

import { useEffect, useRef, useState } from "react";
import type { ComponentProps } from "react";
import { format } from "date-fns";
import type { ClassNames, DateRange } from "react-day-picker";

import { Box, Inline, Stack } from "../ui/box";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Calendar } from "../ui/calendar";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "../ui/command";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Filter,
  X,
  type Glyph,
} from "../ui/glyphs";
import { Icon } from "../ui/icon";
import { Input } from "../ui/input";
import { LABEL_CLASS, Label } from "../ui/label";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "../ui/popover";
import {
  ProviderLogo,
  isProviderLogoId,
} from "../ui/provider-logos";
import { Tabs, TabsList, TabsTrigger } from "../ui/tabs";

import type { ProviderId } from "./provider-mark";

const TABLE_FILTERS_RULES: readonly string[] = [
  "This popover writes request state. It never filters mounted rows. The table renders the page the server returned for the current values.",
  "The trigger is an outline Filters button on the toolbar, with a chip count while any value is set. Applied values also render as Quiet badges under the toolbar: option tone when the cell has one, otherwise info; value provider mark or icon when the option has one, otherwise the column icon, then label, display, remove. Leave a `md` gap above that row so it is not flush with the 32px controls.",
  "The body is Command: first the columns, each with the same icon the grid header uses, then that column's control. Closed enums are static option lists, and each option carries the same glyph or compact ProviderLogo and Quiet tag the cell or row uses. Never derive options from the rows on the page. A 24px ProviderMark well does not belong on this control rung.",
  "Select is one value; choosing it again clears it. Multi-select is a set: the list stays open, every value toggles, and the field holds them joined so the surface can split them back apart. Text, range and dates commit as they are edited. Range and date values encode as `min..max` / `from..to`. A date filter's calendar fills the popover width.",
  "A set is as many pills as it has values, never one pill listing them. Each removes only its own value, the trigger counts values rather than fields, and Reset clears the lot. A single pill reading \"Rep: three names\" makes the reader open the popover to undo one of them, which is the work the pill row exists to save. An option may carry the count of rows behind it, which is a fact about the data and sits quietly after the label; it never replaces the label.",
  "A standing two-position scope (My / All) is tabs on the Account field (or at the top of this popover when the surface has no Account picker). MY is the default applied filter: it counts on the trigger chip and renders as a Quiet pill. ALL is the explicit unscoped catalog, so it does not. Clearing that pill, or Clear filters, writes ALL.",
  "Account is the same kind of field as Owner or Region: one row in the column list. A closed field performs no read. Opening it shows My / All as tabs on that field, then one bounded roster page with the same option glyphs the other selects use. The list reuses the accounts cache when the exact scope and filters match, loads the next cursor as it scrolls, and gives server search its own complete query key. Choosing a row writes the filter and a Quiet pill, same as every other field.",
];

/** Inclusive bounds, dates, and number ranges all use this separator. */
const RANGE_SEPARATOR = "..";

/*
 * What a multi-select field joins its values with. The unit separator, because a value is an opaque
 * server key (an address, a domain, `BOUNCE:POLICY`) and any character a reader could type is a
 * character a key could hold.
 */
const VALUES_SEPARATOR = "\u001f";

/** En dash, the typographic range mark; a hyphen reads as a minus sign here. */
const RANGE_DISPLAY_SEPARATOR = " – ";

/** The one display format in the product. Same constant DatePicker uses. */
const DATE_DISPLAY_FORMAT = "MMM d, yyyy";

const FILTERS_POPOVER_CLASS = "w-72";

const FILTER_CALENDAR_CLASSNAMES: Partial<ClassNames> = {
  months: "flex w-full flex-col",
  month: "flex w-full flex-col gap-3",
  table: "w-full border-collapse",
  head_row: "flex w-full",
  head_cell:
    "flex-1 text-meta font-normal tracking-caps text-ink-subtle uppercase",
  row: "mt-1 flex w-full",
  cell: "relative h-control-sm flex-1 p-0 text-center focus-within:relative focus-within:z-20",
};

const ITEM_CHECK_CLASS =
  "pointer-events-none absolute right-2 flex items-center justify-center";

type BadgeTone = NonNullable<ComponentProps<typeof Badge>["tone"]>;

interface TableFilterOption {
  value: string;
  label: string;
  /** Same glyph the cell uses for this value, when there is one. */
  icon?: Glyph;
  /** Same branded mark the row uses when this value is a provider channel. */
  provider?: ProviderId;
  /** Same Quiet tone the cell uses for this value, when there is one. */
  tone?: BadgeTone;
  /** How many rows carry this value, when the server counted them. A fact, never the label. */
  count?: number;
}

type TableFilterFieldType =
  | "select"
  | "multiselect"
  | "text"
  | "range"
  | "daterange"
  | "catalog";

interface TableFilterField {
  id: string;
  label: string;
  type: TableFilterFieldType;
  /** Same glyph the grid header uses for this column. */
  icon?: Glyph;
  options?: readonly TableFilterOption[];
  placeholder?: string;
  /** Range input labels. Defaults to Minimum / Maximum. */
  minLabel?: string;
  maxLabel?: string;
  /** Catalog rows usually write their label; relationship filters write the opaque id. */
  catalogValue?: "id" | "label";
}

/** Applied filters keyed by field id. Empty or omitted means that field is unset. */
type TableFilterValues = Record<string, string | undefined>;

interface TableFilterTab {
  value: string;
  label: string;
}

/** Standing My / All (or equivalent) at the top of the Filters popover. */
interface TableFilterTabs {
  value: string;
  onValueChange: (value: string) => void;
  items: readonly TableFilterTab[];
  /** Accessible name for the tablist. */
  label: string;
  /** Quiet pill prefix while MY is applied. */
  appliedLabel?: string;
  icon?: Glyph;
}

interface TableFilterCatalogItem {
  id: string;
  label: string;
  description?: string;
  icon?: Glyph;
}

/** Account (or equivalent) finder inside the Filters popover. */
interface TableFilterCatalog {
  /** Exact-read label for a selected value outside the current search page. */
  selectedLabel?: string;
  errorLabel?: string;
  onRetry?: () => void;
  search: string;
  onSearchChange: (value: string) => void;
  /** Activate remote browse/search only while the catalog field is visible. */
  onActiveChange?: (active: boolean) => void;
  items: readonly TableFilterCatalogItem[];
  searchPlaceholder?: string;
  pending?: boolean;
  hasMore?: boolean;
  onFetchMore?: () => void;
  emptyLabel?: string;
  tabs?: TableFilterTabs;
}

interface TableFiltersProps {
  fields: readonly TableFilterField[];
  values: TableFilterValues;
  onChange: (id: string, value: string | undefined) => void;
  onClear?: () => void;
  /** Outline labeled trigger on a table toolbar. Icon-only on a list chrome. */
  trigger?: "button" | "icon";
  /** My / All lives here, not as a column in the list. */
  tabs?: TableFilterTabs;
  /** Account search + roster list under the tabs. */
  catalog?: TableFilterCatalog;
  /** Independent remote catalogs for surfaces with more than one relationship filter. */
  catalogs?: Readonly<Record<string, TableFilterCatalog>>;
}

function isFilterValueSet(value: string | undefined): boolean {
  return value !== undefined && value.length > 0;
}

const APPLIED_SCOPE_VALUE = "MY";
const CLEARED_SCOPE_VALUE = "ALL";

function appliedScopeTabs(
  tabs: TableFilterTabs | undefined
): TableFilterTabs | undefined {
  if (tabs === undefined || tabs.value !== APPLIED_SCOPE_VALUE) return undefined;
  return tabs;
}

function scopeTabLabel(tabs: TableFilterTabs): string {
  return (
    tabs.items.find((item) => item.value === tabs.value)?.label ??
    "My accounts"
  );
}

function scopePillLabel(tabs: TableFilterTabs): string {
  const value = scopeTabLabel(tabs);
  return tabs.appliedLabel === undefined
    ? value
    : `${tabs.appliedLabel}: ${value}`;
}

/** A set of values as one field value, or nothing at all for the empty set. */
function joinValues(values: readonly string[]): string | undefined {
  const kept = values.filter((value) => value.length > 0);
  return kept.length === 0 ? undefined : kept.join(VALUES_SEPARATOR);
}

/** The values a multi-select field holds, in the order they were chosen. */
function splitValues(value?: string): string[] {
  if (value === undefined || value.length === 0) return [];
  return value.split(VALUES_SEPARATOR).filter((part) => part.length > 0);
}

/** One value added to a set, or taken off it when the set already holds it. */
function toggleValue(value: string | undefined, next: string): string | undefined {
  const current = splitValues(value);
  return joinValues(
    current.includes(next) ? current.filter((held) => held !== next) : [...current, next]
  );
}

/**
 * How many filters are applied, which is how many the trigger and the category row count. A set
 * counts its values rather than itself: two reps chosen is two filters as far as a reader is
 * concerned, and it is two pills.
 *
 * Only a value with a field counts. A surface may keep a value in its URL while the field that
 * writes it is hidden (Accounts keeps `de_assignment` after a DE rep leaves the MY scope; the Inbox
 * keeps `account` and `account_name` beside fields it does not always offer). Counting it would put
 * a number on the trigger and a Clear footer in the popover with no pill anywhere to account for
 * them, so the count walks the fields, exactly as the pills do.
 */
function countFilterValues(values: TableFilterValues, fields: readonly TableFilterField[]): number {
  return fields
    .filter((field) => isFilterValueSet(values[field.id]))
    .reduce(
      (total, field) => total + (field.type === "multiselect" ? splitValues(values[field.id]).length : 1),
      0
    );
}

function joinRange(min?: string, max?: string): string | undefined {
  const start = min?.trim() ?? "";
  const end = max?.trim() ?? "";
  if (start.length === 0 && end.length === 0) return undefined;
  return `${start}${RANGE_SEPARATOR}${end}`;
}

function splitRange(value?: string): { min: string; max: string } {
  if (value === undefined || value.length === 0) {
    return { min: "", max: "" };
  }
  const at = value.indexOf(RANGE_SEPARATOR);
  if (at === -1) return { min: value, max: "" };
  return { min: value.slice(0, at), max: value.slice(at + RANGE_SEPARATOR.length) };
}

function formatIsoDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function parseIsoDate(value: string): Date | undefined {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (match === null) return undefined;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (!year || !month || !day) return undefined;
  return new Date(year, month - 1, day);
}

function rangeToDateRange(value?: string): DateRange | undefined {
  const { min, max } = splitRange(value);
  const from = min.length > 0 ? parseIsoDate(min) : undefined;
  const to = max.length > 0 ? parseIsoDate(max) : undefined;
  if (from === undefined && to === undefined) return undefined;
  return { from, to };
}

function formatFilterValue(
  field: TableFilterField,
  value: string,
  catalog?: TableFilterCatalog
): string {
  if (field.type === "catalog") {
    return (
      catalog?.items.find((item) => item.id === value || item.label === value)
        ?.label ?? catalog?.selectedLabel ?? value
    );
  }
  if (field.type === "select" || field.type === "multiselect") {
    return field.options?.find((option) => option.value === value)?.label ?? value;
  }
  if (field.type === "range") {
    const { min, max } = splitRange(value);
    if (min.length > 0 && max.length > 0) {
      return `${min}${RANGE_DISPLAY_SEPARATOR}${max}`;
    }
    if (min.length > 0) return `${min}+`;
    if (max.length > 0) return `Up to ${max}`;
    return value;
  }
  if (field.type === "daterange") {
    const { min, max } = splitRange(value);
    const from = min.length > 0 ? parseIsoDate(min) : undefined;
    const to = max.length > 0 ? parseIsoDate(max) : undefined;
    const fromLabel =
      from === undefined ? min : format(from, DATE_DISPLAY_FORMAT);
    const toLabel = to === undefined ? max : format(to, DATE_DISPLAY_FORMAT);
    if (fromLabel.length > 0 && toLabel.length > 0) {
      if (min === max) return fromLabel;
      return `${fromLabel}${RANGE_DISPLAY_SEPARATOR}${toLabel}`;
    }
    if (fromLabel.length > 0) return `From ${fromLabel}`;
    if (toLabel.length > 0) return `Until ${toLabel}`;
    return value;
  }
  return value;
}

function FilterBackButton({
  icon,
  label,
  onBack,
}: {
  icon?: Glyph;
  label: string;
  onBack: () => void;
}) {
  return (
    <Inline
      gap="sm"
      align="center"
      className="h-control border-b border-line px-1.5"
    >
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label="Back to filters"
        onClick={onBack}
      >
        <Icon icon={ChevronLeft} size="sm" />
      </Button>
      {icon === undefined ? null : (
        <Icon icon={icon} size="sm" className="text-ink-subtle" />
      )}
      <Box render={<span />} className={LABEL_CLASS}>
        {label}
      </Box>
    </Inline>
  );
}

interface TextFilterDraft {
  source: string | undefined;
  draft: string;
}

function TextFilterControl({
  field,
  value,
  onChange,
}: {
  field: TableFilterField;
  value: string | undefined;
  onChange: (next: string | undefined) => void;
}) {
  const [draftState, setDraftState] = useState<TextFilterDraft>({
    source: value,
    draft: value ?? "",
  });
  const timeoutRef = useRef<number | null>(null);
  const draft = draftState.source === value ? draftState.draft : (value ?? "");

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

  function commit(next: string): void {
    const trimmed = next.trim();
    onChange(trimmed.length === 0 ? undefined : trimmed);
  }

  return (
    <Box className="p-2">
      <Input
        value={draft}
        placeholder={field.placeholder ?? field.label}
        aria-label={field.label}
        onChange={(event) => {
          const next = event.target.value;
          setDraftState({ source: value, draft: next });
          if (timeoutRef.current !== null) {
            window.clearTimeout(timeoutRef.current);
          }
          timeoutRef.current = window.setTimeout(() => {
            timeoutRef.current = null;
            commit(next);
          }, 300);
        }}
      />
    </Box>
  );
}

function RangeFilterControl({
  field,
  value,
  onChange,
}: {
  field: TableFilterField;
  value: string | undefined;
  onChange: (next: string | undefined) => void;
}) {
  const { min, max } = splitRange(value);

  return (
    <Stack gap="sm" className="p-2">
      <Stack gap="xs">
        <Label htmlFor={`${field.id}-min`}>{field.minLabel ?? "Minimum"}</Label>
        <Input
          id={`${field.id}-min`}
          type="number"
          inputMode="decimal"
          value={min}
          placeholder="Min"
          onChange={(event) =>
            onChange(joinRange(event.target.value, max))
          }
        />
      </Stack>
      <Stack gap="xs">
        <Label htmlFor={`${field.id}-max`}>{field.maxLabel ?? "Maximum"}</Label>
        <Input
          id={`${field.id}-max`}
          type="number"
          inputMode="decimal"
          value={max}
          placeholder="Max"
          onChange={(event) =>
            onChange(joinRange(min, event.target.value))
          }
        />
      </Stack>
    </Stack>
  );
}

function DateRangeFilterControl({
  value,
  onChange,
  onComplete,
}: {
  value: string | undefined;
  onChange: (next: string | undefined) => void;
  onComplete: () => void;
}) {
  const selected = rangeToDateRange(value);

  return (
    <Calendar
      mode="range"
      selected={selected}
      defaultMonth={selected?.from}
      className="w-full"
      classNames={FILTER_CALENDAR_CLASSNAMES}
      onSelect={(next) => {
        if (next?.from === undefined) {
          onChange(undefined);
          return;
        }
        const from = formatIsoDate(next.from);
        const to = next.to === undefined ? from : formatIsoDate(next.to);
        onChange(joinRange(from, to));
        if (next.to !== undefined) onComplete();
      }}
    />
  );
}

function FilterOptionLeading({
  icon,
  provider,
}: {
  icon?: Glyph;
  provider?: ProviderId;
}) {
  if (provider !== undefined && isProviderLogoId(provider)) {
    return (
      <Box render={<span />} data-provider={provider} aria-hidden>
        <ProviderLogo provider={provider} className="size-3.5" />
      </Box>
    );
  }
  if (icon === undefined) {
    return null;
  }
  return <Icon icon={icon} size="sm" />;
}

function SelectFilterControl({
  field,
  value,
  onChange,
}: {
  field: TableFilterField;
  value: string | undefined;
  onChange: (next: string | undefined) => void;
}) {
  const options = field.options ?? [];

  return (
    <Command>
      <CommandInput placeholder={`Filter ${field.label}`} />
      <CommandList>
        <CommandEmpty>Nothing matches.</CommandEmpty>
        <CommandGroup>
          {options.map((option) => {
            const selected = option.value === value;
            return (
              <CommandItem
                key={option.value}
                value={option.label}
                onSelect={() => onChange(selected ? undefined : option.value)}
                className={selected ? "pr-8" : undefined}
              >
                <Badge
                  tier="quiet"
                  tone={option.tone ?? "neutral"}
                  className="min-w-0"
                >
                  <FilterOptionLeading
                    icon={option.icon}
                    provider={option.provider}
                  />
                  <Box render={<span />} className="truncate">
                    {option.label}
                  </Box>
                </Badge>
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
  );
}

/**
 * A set of values: the same command list as a select, except that choosing does not close it and a
 * chosen value stays visible with its check, so a reader building a set of four reps does it in one
 * opening rather than four.
 */
function MultiSelectFilterControl({
  field,
  value,
  onChange,
}: {
  field: TableFilterField;
  value: string | undefined;
  onChange: (next: string | undefined) => void;
}) {
  const chosen = splitValues(value);

  return (
    <Command>
      <CommandInput placeholder={`Filter ${field.label}`} />
      <CommandList>
        <CommandEmpty>Nothing matches.</CommandEmpty>
        <CommandGroup>
          {(field.options ?? []).map((option) => {
            const selected = chosen.includes(option.value);
            return (
              <CommandItem
                key={option.value}
                value={option.label}
                onSelect={() => onChange(toggleValue(value, option.value))}
                className="pr-8"
                data-selected-value={selected ? "true" : undefined}
              >
                <Badge tier="quiet" tone={option.tone ?? "neutral"} className="min-w-0">
                  <FilterOptionLeading icon={option.icon} provider={option.provider} />
                  <Box render={<span />} className="truncate">
                    {option.label}
                  </Box>
                </Badge>
                {option.count === undefined ? null : (
                  <Box
                    render={<span />}
                    className="ml-auto shrink-0 font-mono text-meta tabular-nums text-ink-subtle"
                  >
                    {option.count.toLocaleString("en-US")}
                  </Box>
                )}
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
  );
}

function FieldDetail({
  catalog,
  field,
  value,
  onBack,
  onChange,
}: {
  catalog?: TableFilterCatalog;
  field: TableFilterField;
  value: string | undefined;
  onBack: () => void;
  onChange: (next: string | undefined) => void;
}) {
  return (
    <Stack>
      <FilterBackButton icon={field.icon} label={field.label} onBack={onBack} />
      {field.type === "catalog" && catalog !== undefined ? (
        <CatalogFilterControl
          catalog={catalog}
          field={field}
          value={value}
          onChange={(next) => {
            onChange(next);
            onBack();
          }}
        />
      ) : null}
      {field.type === "multiselect" ? (
        <MultiSelectFilterControl field={field} value={value} onChange={onChange} />
      ) : null}
      {field.type === "select" ? (
        <SelectFilterControl
          field={field}
          value={value}
          onChange={(next) => {
            onChange(next);
            onBack();
          }}
        />
      ) : null}
      {field.type === "text" ? (
        <TextFilterControl field={field} value={value} onChange={onChange} />
      ) : null}
      {field.type === "range" ? (
        <RangeFilterControl field={field} value={value} onChange={onChange} />
      ) : null}
      {field.type === "daterange" ? (
        <DateRangeFilterControl
          value={value}
          onChange={onChange}
          onComplete={onBack}
        />
      ) : null}
    </Stack>
  );
}

function CategoryList({
  catalog,
  fields,
  values,
  onSelect,
}: {
  catalog?: TableFilterCatalog;
  fields: readonly TableFilterField[];
  values: TableFilterValues;
  onSelect: (id: string) => void;
}) {
  return (
    <Command>
      <CommandInput placeholder="Filter by" />
      <CommandList>
        <CommandEmpty>No filters match.</CommandEmpty>
        <CommandGroup>
          {fields.map((field) => {
            const set =
              isFilterValueSet(values[field.id]) ||
              (field.type === "catalog" &&
                appliedScopeTabs(catalog?.tabs) !== undefined);
            return (
              <CommandItem
                key={field.id}
                value={field.label}
                onSelect={() => onSelect(field.id)}
              >
                {field.icon === undefined ? null : (
                  <Icon icon={field.icon} size="sm" className="text-ink-subtle" />
                )}
                <span className="min-w-0 flex-1 truncate">{field.label}</span>
                {set ? (
                  <Badge tier="chip" className="ml-auto" aria-hidden>
                    {field.type === "multiselect" ? splitValues(values[field.id]).length : 1}
                  </Badge>
                ) : (
                  <Icon
                    icon={ChevronRight}
                    size="sm"
                    className="ml-auto text-ink-subtle"
                  />
                )}
              </CommandItem>
            );
          })}
        </CommandGroup>
      </CommandList>
    </Command>
  );
}

function FilterCatalogSentinel({
  enabled,
  onVisible,
}: {
  enabled: boolean;
  onVisible: () => void;
}) {
  const nodeRef = useRef<HTMLDivElement | null>(null);
  const onVisibleRef = useRef(onVisible);

  useEffect(() => {
    onVisibleRef.current = onVisible;
  }, [onVisible]);

  useEffect(() => {
    if (!enabled) return;
    const node = nodeRef.current;
    if (node === null) return;
    if (typeof IntersectionObserver === "undefined") return;
    const root = node.closest("[data-slot=scroll-area-viewport]");
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) onVisibleRef.current();
      },
      {
        root: root instanceof Element ? root : null,
        rootMargin: "200px",
      }
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [enabled]);

  return (
    <Box
      render={<div ref={nodeRef} />}
      data-slot="filter-catalog-load-more"
      aria-hidden
      className="h-px w-full"
    />
  );
}

function CatalogFilterControl({
  catalog,
  field,
  value,
  onChange,
}: {
  catalog: TableFilterCatalog;
  field: TableFilterField;
  value: string | undefined;
  onChange: (next: string | undefined) => void;
}) {
  const pendingEmpty = catalog.pending === true && catalog.items.length === 0;
  const placeholder =
    catalog.searchPlaceholder ?? `Filter ${field.label}`;

  return (
    <Stack>
      {catalog.tabs === undefined ? null : (
        <FilterScopeTabs tabs={catalog.tabs} />
      )}
      <Command shouldFilter={false}>
        <CommandInput
          value={catalog.search}
          onValueChange={catalog.onSearchChange}
          placeholder={placeholder}
        />
        <CommandList>
          {catalog.errorLabel ? (
            <Inline gap="sm" padding="md" align="center">
              <Box render={<span />} className="text-meta text-ink-subtle">{catalog.errorLabel}</Box>
              <Button variant="ghost" size="compact" onClick={catalog.onRetry}>Retry</Button>
            </Inline>
          ) : pendingEmpty ? (
            <p className="px-2.5 py-8 text-center text-label text-ink-subtle">
              Searching…
            </p>
          ) : catalog.items.length === 0 ? (
            <p className="px-2.5 py-8 text-center text-label text-ink-subtle">
              {catalog.emptyLabel ?? "Nothing matches."}
            </p>
          ) : (
            <CommandGroup>
              {catalog.items.map((item) => {
                const itemValue =
                  field.catalogValue === "id" ? item.id : item.label;
                const selected = itemValue === value;
                const icon = item.icon ?? field.icon;
                return (
                  <CommandItem
                    key={item.id}
                    value={item.id}
                    onSelect={() =>
                      onChange(selected ? undefined : itemValue)
                    }
                    className={selected ? "pr-8" : undefined}
                  >
                    {icon === undefined ? null : (
                      <Icon
                        icon={icon}
                        size="sm"
                        className="text-ink-subtle"
                      />
                    )}
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">{item.label}</span>
                      {item.description === undefined ||
                      item.description.length === 0 ? null : (
                        <span className="block truncate text-meta text-ink-subtle">
                          {item.description}
                        </span>
                      )}
                    </span>
                    {selected ? (
                      <span className={ITEM_CHECK_CLASS}>
                        <Icon icon={Check} size="sm" />
                      </span>
                    ) : null}
                  </CommandItem>
                );
              })}
            </CommandGroup>
          )}
          {catalog.hasMore === true && catalog.onFetchMore !== undefined ? (
            <FilterCatalogSentinel enabled onVisible={catalog.onFetchMore} />
          ) : null}
        </CommandList>
      </Command>
    </Stack>
  );
}

function FilterScopeTabs({ tabs }: { tabs: TableFilterTabs }) {
  return (
    <Box className="border-b border-line px-1.5 pt-1.5 pb-1">
      <Tabs
        value={tabs.value}
        onValueChange={(next) => tabs.onValueChange(next)}
      >
        <TabsList
          variant="default"
          aria-label={tabs.label}
          className="w-full"
        >
          {tabs.items.map((item) => (
            <TabsTrigger key={item.value} value={item.value}>
              {item.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
    </Box>
  );
}

function TableFilters({
  fields,
  values,
  onChange,
  onClear,
  trigger = "button",
  tabs,
  catalog,
  catalogs,
}: TableFiltersProps) {
  const [open, setOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = fields.find((field) => field.id === selectedId);
  const selectedCatalog =
    selected === undefined ? undefined : (catalogs?.[selected.id] ?? catalog);
  const catalogActive = open && selected?.type === "catalog";
  const scopeTabs = catalog?.tabs ?? tabs;
  const scopeApplied = appliedScopeTabs(scopeTabs) !== undefined;
  const activeCount = countFilterValues(values, fields) + (scopeApplied ? 1 : 0);
  const iconOnly = trigger === "icon";

  useEffect(() => {
    const onActiveChange = selectedCatalog?.onActiveChange;
    if (onActiveChange === undefined) return;
    onActiveChange(catalogActive);
    return () => onActiveChange(false);
  }, [catalogActive, selectedCatalog?.onActiveChange]);

  function handleOpenChange(next: boolean): void {
    setOpen(next);
    if (!next) setSelectedId(null);
  }

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger
        render={
          <Button
            type="button"
            variant={iconOnly ? "ghost" : "outline"}
            size={iconOnly ? "icon-sm" : undefined}
          />
        }
        aria-label={
          activeCount > 0 ? `Filters, ${activeCount} applied` : "Filters"
        }
      >
        <Icon icon={Filter} size="sm" />
        {iconOnly ? null : (
          <>
            Filters
            {activeCount > 0 ? <Badge tier="chip">{activeCount}</Badge> : null}
          </>
        )}
      </PopoverTrigger>
      <PopoverContent
        align="end"
        inset="flush"
        className={FILTERS_POPOVER_CLASS}
      >
        {tabs === undefined || selected !== undefined ? null : (
          <FilterScopeTabs tabs={tabs} />
        )}
        {selected === undefined ? (
          fields.length === 0 ? null : (
            <CategoryList
              catalog={catalog}
              fields={fields}
              values={values}
              onSelect={setSelectedId}
            />
          )
        ) : (
          <FieldDetail
            catalog={selectedCatalog}
            field={selected}
            value={values[selected.id]}
            onBack={() => setSelectedId(null)}
            onChange={(next) => onChange(selected.id, next)}
          />
        )}
        {activeCount === 0 ||
        selected !== undefined ||
        (onClear === undefined && !scopeApplied) ? null : (
          <Box className="border-t border-line p-1.5">
            <Button
              variant="ghost"
              className="w-full"
              onClick={() => {
                if (onClear !== undefined) {
                  onClear();
                  return;
                }
                scopeTabs?.onValueChange(CLEARED_SCOPE_VALUE);
              }}
            >
              Clear filters
            </Button>
          </Box>
        )}
      </PopoverContent>
    </Popover>
  );
}

function TableFilterPills({
  fields,
  values,
  onChange,
  tabs,
  catalog,
  catalogs,
}: {
  fields: readonly TableFilterField[];
  values: TableFilterValues;
  onChange: (id: string, value: string | undefined) => void;
  tabs?: TableFilterTabs;
  catalog?: TableFilterCatalog;
  catalogs?: Readonly<Record<string, TableFilterCatalog>>;
}) {
  const scope = appliedScopeTabs(tabs);
  const active = fields.filter((field) => isFilterValueSet(values[field.id]));
  if (active.length === 0 && scope === undefined) return null;

  return (
    <Inline
      gap="xs"
      wrap
      align="center"
      data-slot="table-filter-pills"
    >
      {scope === undefined ? null : (
        <Badge key="scope" tier="quiet" tone="info">
          {scope.icon === undefined ? null : (
            <Icon icon={scope.icon} size="sm" />
          )}
          <Box render={<span />}>{scopePillLabel(scope)}</Box>
          <Box
            render={<button type="button" />}
            aria-label={`Remove ${scope.appliedLabel ?? scopeTabLabel(scope)} filter`}
            className="inline-flex size-3.5 items-center justify-center text-ink-subtle hover:text-ink"
            onClick={() => scope.onValueChange(CLEARED_SCOPE_VALUE)}
          >
            <Icon icon={X} size="sm" />
          </Box>
        </Badge>
      )}
      {active.flatMap((field) => {
        const value = values[field.id];
        if (value === undefined) return [];
        /* A set is as many pills as it has values, and each one removes only itself. */
        const set = field.type === "multiselect";
        const held = set ? splitValues(value) : [value];
        return held.map((one) => {
          const selected = field.options?.find((option) => option.value === one);
          const shown = formatFilterValue(field, one, catalogs?.[field.id] ?? catalog);
          return (
            <Badge key={`${field.id}:${one}`} tier="quiet" tone={selected?.tone ?? "info"}>
              <FilterOptionLeading
                icon={selected?.icon ?? field.icon}
                provider={selected?.provider}
              />
              <Box render={<span />}>{`${field.label}: ${shown}`}</Box>
              <Box
                render={<button type="button" />}
                /* A set has several pills, so its remove control names the value it takes off. */
                aria-label={set ? `Remove ${field.label} ${shown} filter` : `Remove ${field.label} filter`}
                className="inline-flex size-3.5 items-center justify-center text-ink-subtle hover:text-ink"
                onClick={() => onChange(field.id, set ? toggleValue(value, one) : undefined)}
              >
                <Icon icon={X} size="sm" />
              </Box>
            </Badge>
          );
        });
      })}
    </Inline>
  );
}

export {
  TableFilters,
  TableFilterPills,
  TABLE_FILTERS_RULES,
  RANGE_SEPARATOR,
  VALUES_SEPARATOR,
  appliedScopeTabs,
  countFilterValues,
  formatFilterValue,
  isFilterValueSet,
  joinRange,
  joinValues,
  splitRange,
  splitValues,
  toggleValue,
};
export type {
  TableFilterField,
  TableFilterFieldType,
  TableFilterOption,
  TableFilterCatalog,
  TableFilterCatalogItem,
  TableFilterTab,
  TableFilterTabs,
  TableFilterValues,
  TableFiltersProps,
};
