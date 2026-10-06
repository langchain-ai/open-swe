"use client";

/*
 * Rules for OverflowTabs.
 *
 * Narrow rails (Inbox list sidebar at 384 / 320) cannot afford a horizontal
 * ScrollArea on a status tab strip — a track + gutter in the one pane that
 * least can spend width. The established pattern (ServiceNow Horizon, Nuxt UI
 * collapse, CSS-Tricks adapting tabs) is: fit what you can, park the rest
 * under Other + Popover. Never wrap TabsList in overflow-x scroll.
 *
 * Parking changes geometry immediately. Grid-track animation would run layout
 * on every frame while the list pane resizes.
 */

import {
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { Box, Inline } from "../ui/box";
import { Button } from "../ui/button";
import { cn } from "../ui/cn";
import { ChevronDown, type Glyph } from "../ui/glyphs";
import { Icon } from "../ui/icon";
import { IconWell } from "../ui/icon-well";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "../ui/popover";
import { Tabs, TabsList, TabsTrigger } from "../ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "../ui/tooltip";

const OVERFLOW_TABS_RULES: readonly string[] = [
  "Fit tabs that fit; park the rest under Other + Popover. Never a horizontal ScrollArea on a tab strip — especially not inside a narrow list sidebar.",
  "The selected tab always stays visible. If it would overflow, it swaps into the visible set and something else goes under Other.",
  "Other is a Popover of the hidden tabs, not a second TabsList and not a route. Choosing an item selects that tab and closes the popover.",
  "Callers may name the overflow menu and mark secondary views overflowOnly. A selected secondary view is promoted to a visible tab. One description supplies both the tab tooltip and menu explanation.",
  "An item's icon identifies it in the menu. `icons` also draws it on the visible triggers, for a strip whose options are a vocabulary the reader already meets elsewhere in the same view: the glyph on the tab is then the glyph on the row it filters or groups. It is per strip, never per item, because a set where only some tabs carry a glyph reads as tabs with something missing. The measure row draws the same glyph, so a tab is never parked for width the live trigger does not spend.",
  "Compose real `Tabs` / `TabsList` / `TabsTrigger` for the visible set so keyboard and active treatment stay on the primitive. `variant` selects the TabsList family: `default` is the rounded segmented track; `ghost` is floating fully-rounded pills (active capsule, no track); `line` is the underline rail.",
  "TabsList is `w-fit`, triggers are `flex-none`, Other is `shrink-0`. Never `w-full` on the list beside Other — that steals the Other lane and paints the last badge over the word Other (the '0 ther' tell).",
  "Measure with the live trigger geometry (`h-control-sm` + `px-2.5` + badge). A thinner measure row under-counts width and parks tabs that would have fit.",
  "Tabs stay mounted while parking changes grid tracks immediately. Resizing and keyboard selection never wait for layout animation.",
];

/** Horizontal disclose: 0fr parks the tab, 1fr reveals it — same gesture as Collapsible. */
const TAB_SLOT_CLASS =
  "grid min-w-0";
const TAB_SLOT_OPEN_CLASS = "grid-cols-[1fr] opacity-100";
const TAB_SLOT_PARKED_CLASS =
  "pointer-events-none grid-cols-[0fr] opacity-0";
const TAB_SLOT_INNER_CLASS = "min-w-0 overflow-hidden";

const OTHER_SLOT_CLASS =
  "grid";
const OTHER_SLOT_OPEN_CLASS = "grid-cols-[1fr] opacity-100";
const OTHER_SLOT_CLOSED_CLASS =
  "pointer-events-none grid-cols-[0fr] opacity-0";

type OverflowTabsVariant = "default" | "ghost" | "line";

interface OverflowTabItem {
  value: string;
  label: string;
  /** One explanation shared by the tab tooltip and overflow choice. */
  description?: string;
  /** Visual identity in the menu; visible tabs keep their compact text labels. */
  icon?: Glyph;
  /** Secondary views stay in the menu unless selected. */
  overflowOnly?: boolean;
  /** Quiet count / chip — same slot TabsTrigger already accepts. */
  badge?: ReactNode;
}

interface OverflowTabsProps {
  items: readonly OverflowTabItem[];
  value: string;
  onValueChange: (next: string) => void;
  /** Accessible name for the visible tablist. */
  label: string;
  overflowLabel?: string;
  /**
   * TabsList family. Inbox status uses floating pills (`ghost`) — rounded
   * active selector, no segmented track fill. Pass `default` for the track,
   * `line` for the flush underline rail.
   */
  variant?: OverflowTabsVariant;
  /**
   * Draw each item's icon on its visible trigger, not only in the overflow
   * menu. For a strip whose options are a vocabulary the view repeats.
   */
  icons?: boolean;
  /**
   * Test / story escape hatch: force at most N visible tabs (Other holds the
   * rest). Production leaves this unset and measures the container.
   */
  maxVisible?: number;
  className?: string;
}

const GAP_PX = 4;

/** Pure split used by the measure effect and by unit tests. */
function splitOverflowTabs(
  items: readonly OverflowTabItem[],
  widths: readonly number[],
  selected: string,
  available: number,
  otherWidth: number,
  gap: number
): { visible: OverflowTabItem[]; overflow: OverflowTabItem[] } {
  const total = items.length;
  if (total === 0) {
    return { visible: [], overflow: [] };
  }

  const widthOf = (indices: readonly number[], withOther: boolean) => {
    let w = 0;
    indices.forEach((idx, i) => {
      w += (widths[idx] ?? 0) + (i > 0 ? gap : 0);
    });
    if (withOther) {
      w += (indices.length > 0 ? gap : 0) + otherWidth;
    }
    return w;
  };

  const allIdx = items.flatMap((item, i) => !item.overflowOnly || item.value === selected ? [i] : []);
  const hasSecondary = allIdx.length < total;
  if (widthOf(allIdx, hasSecondary) <= available) {
    return { visible: allIdx.map((i) => items[i]!), overflow: items.filter((_, i) => !allIdx.includes(i)) };
  }

  let n = allIdx.length - 1;
  while (n >= 1 && widthOf(allIdx.slice(0, n), true) > available) {
    n -= 1;
  }
  n = Math.max(1, n);

  let visibleIdx = allIdx.slice(0, n);
  const selectedIndex = items.findIndex((item) => item.value === selected);

  if (selectedIndex >= 0 && !visibleIdx.includes(selectedIndex)) {
    visibleIdx = [...visibleIdx.slice(0, -1), selectedIndex].sort(
      (a, b) => a - b
    );
    while (visibleIdx.length > 1 && widthOf(visibleIdx, true) > available) {
      const dropAt = [...visibleIdx]
        .reverse()
        .find((idx) => idx !== selectedIndex);
      if (dropAt === undefined) break;
      visibleIdx = visibleIdx.filter((idx) => idx !== dropAt);
    }
  }

  const visibleSet = new Set(visibleIdx);
  return {
    visible: visibleIdx.map((idx) => items[idx]!),
    overflow: items.filter((_, idx) => !visibleSet.has(idx)),
  };
}

function forceMaxVisible(
  items: readonly OverflowTabItem[],
  selected: string,
  maxVisible: number
): { visible: OverflowTabItem[]; overflow: OverflowTabItem[] } {
  const n = Math.max(1, Math.min(maxVisible, items.length));
  let visibleIdx = items.flatMap((item, i) => !item.overflowOnly || item.value === selected ? [i] : []).slice(0, n);
  const selectedIndex = items.findIndex((item) => item.value === selected);
  if (selectedIndex >= 0 && !visibleIdx.includes(selectedIndex)) {
    visibleIdx = [...visibleIdx.slice(0, -1), selectedIndex].sort(
      (a, b) => a - b
    );
  }
  const visibleSet = new Set(visibleIdx);
  return {
    visible: visibleIdx.map((idx) => items[idx]!),
    overflow: items.filter((_, idx) => !visibleSet.has(idx)),
  };
}

function OverflowTabs({
  className,
  icons = false,
  items,
  label,
  maxVisible,
  overflowLabel = "Other",
  onValueChange,
  value,
  variant = "line",
}: OverflowTabsProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const measureRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [split, setSplit] = useState(() =>
    maxVisible !== undefined
      ? forceMaxVisible(items, value, maxVisible)
      : forceMaxVisible(items, value, items.length)
  );

  useLayoutEffect(() => {
    const root = rootRef.current;
    const measure = measureRef.current;
    if (!root || !measure) return;

    const recompute = () => {
      if (maxVisible !== undefined) {
        setSplit(forceMaxVisible(items, value, maxVisible));
        return;
      }

      const tabEls = [
        ...measure.querySelectorAll<HTMLElement>("[data-measure-tab]"),
      ];
      const widths = tabEls.map((el) => el.getBoundingClientRect().width);
      const otherEl = measure.querySelector<HTMLElement>("[data-measure-other]");
      const otherWidth = otherEl?.getBoundingClientRect().width ?? 64;
      const available = root.clientWidth;

      /*
       * jsdom reports 0×0 — show everything rather than inventing an Other
       * that production measurement would not. Real layout always re-runs.
       */
      if (available === 0 || widths.every((w) => w === 0)) {
        setSplit(forceMaxVisible(items, value, items.length));
        return;
      }

      setSplit(
        splitOverflowTabs(
          items,
          widths,
          value,
          available,
          otherWidth,
          GAP_PX
        )
      );
    };

    recompute();

    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(recompute);
    observer.observe(root);
    return () => observer.disconnect();
  }, [items, maxVisible, value, overflowLabel]);

  const overflowValues = new Set(split.overflow.map((item) => item.value));
  const otherOpen = split.overflow.length > 0;

  return (
    <div
      ref={rootRef}
      data-slot="overflow-tabs"
      data-testid="overflow-tabs"
      className={cn("relative w-full min-w-0", className)}
    >
      {/*
       * Off-screen measure row: every tab + Other at the SAME geometry as the
       * live triggers (px-2.5 / h-control-sm). Never `h-0` — that under-reports
       * widths and parks tabs that would have fit.
       */}
      <div
        ref={measureRef}
        aria-hidden
        className="pointer-events-none invisible absolute left-0 top-0 -z-10 flex items-center gap-1"
      >
        {items.map((item) => (
          <span
            key={item.value}
            data-measure-tab={item.value}
            className="inline-flex h-control-sm shrink-0 items-center gap-1.5 px-2.5 text-label font-medium whitespace-nowrap"
          >
            {icons && item.icon !== undefined ? <Icon icon={item.icon} size="sm" /> : null}
            {item.label}
            {item.badge ? (
              <span className="shrink-0">{item.badge}</span>
            ) : null}
          </span>
        ))}
        <span
          data-measure-other=""
          className="inline-flex h-control-sm shrink-0 items-center gap-1 px-2.5 text-label font-medium whitespace-nowrap"
        >
          {overflowLabel}
          <Icon icon={ChevronDown} size="sm" />
        </span>
      </div>

      <Tabs
        value={value}
        onValueChange={onValueChange}
        className="min-w-0 gap-0"
      >
        {/*
         * Every tab stays mounted. Parking changes grid tracks immediately
         * without remounting the tabs or interpolating layout.
         */}
        <Inline gap="xs" align="center" className="min-w-0 w-full">
          <TabsList
            variant={variant}
            className={cn(
              "min-w-0 w-fit max-w-full justify-start overflow-hidden",
              variant === "line" ? "h-row-record" : undefined
            )}
            aria-label={label}
          >
            {items.map((item) => {
              const parked = overflowValues.has(item.value);
              const trigger = (
                <TabsTrigger value={item.value} tabIndex={parked ? -1 : undefined}
                  aria-hidden={parked || undefined} className="flex-none">
                  {icons && item.icon !== undefined ? <Icon icon={item.icon} size="sm" /> : null}
                  <span className="truncate">{item.label}</span>
                  {item.badge ? <span className="shrink-0">{item.badge}</span> : null}
                </TabsTrigger>
              );
              return (
                <Box
                  key={item.value}
                  data-parked={parked ? "true" : "false"}
                  className={cn(
                    TAB_SLOT_CLASS,
                    parked ? TAB_SLOT_PARKED_CLASS : TAB_SLOT_OPEN_CLASS
                  )}
                >
                  <Box className={TAB_SLOT_INNER_CLASS}>
                    {item.description && !parked ? (
                      <Tooltip>
                        <TooltipTrigger render={trigger} />
                        <TooltipContent side="bottom">{item.description}</TooltipContent>
                      </Tooltip>
                    ) : trigger}
                  </Box>
                </Box>
              );
            })}
          </TabsList>

          <Box
            data-slot="overflow-tabs-other"
            data-open={otherOpen ? "true" : "false"}
            className={cn(
              OTHER_SLOT_CLASS,
              otherOpen ? OTHER_SLOT_OPEN_CLASS : OTHER_SLOT_CLOSED_CLASS
            )}
          >
            <Box className="min-w-0 overflow-hidden">
              <Popover open={open} onOpenChange={setOpen}>
                <PopoverTrigger
                  render={
                    <Button
                      type="button"
                      variant="ghost"
                      size="compact"
                      aria-label={`${overflowLabel} status filters`}
                      aria-hidden={!otherOpen || undefined}
                      tabIndex={otherOpen ? undefined : -1}
                      className={cn(
                        "h-control-sm shrink-0 gap-1 px-2.5 text-label font-medium text-ink-subtle",
                        variant === "line"
                          ? "rounded-none"
                          : variant === "ghost"
                            ? "rounded-full"
                            : "rounded-compact"
                      )}
                    />
                  }
                >
                  {overflowLabel}
                  <Icon icon={ChevronDown} size="sm" />
                </PopoverTrigger>
                <PopoverContent
                  align="end"
                  side="bottom"
                  sideOffset={4}
                  inset="flush"
                  className={cn("min-w-44 p-1", split.overflow.some((item) => item.description) && "w-80 max-w-full")}
                >
                  <Box
                    render={<div />}
                    role="listbox"
                    aria-label={`${overflowLabel} status filters`}
                    className="flex flex-col gap-0.5"
                  >
                    {split.overflow.map((item) => (
                      <Button
                        key={item.value}
                        type="button"
                        variant="ghost"
                        size="compact"
                        role="option"
                        aria-selected={item.value === value}
                        className={cn("w-full justify-between gap-2 rounded-badge px-2.5", item.description && "h-auto py-2.5")}
                        onClick={() => {
                          onValueChange(item.value);
                          setOpen(false);
                        }}
                      >
                        {item.icon ? <IconWell><Icon icon={item.icon} size="sm" /></IconWell> : null}
                        <Box className="min-w-0 flex-1 text-left">
                          <span>{item.label}</span>
                          {item.description ? <p className="truncate text-meta font-normal text-ink-subtle" title={item.description}>{item.description}</p> : null}
                        </Box>
                        {item.badge ? (
                          <span className="shrink-0">{item.badge}</span>
                        ) : null}
                      </Button>
                    ))}
                  </Box>
                </PopoverContent>
              </Popover>
            </Box>
          </Box>
        </Inline>
      </Tabs>
    </div>
  );
}

export {
  OverflowTabs,
  OVERFLOW_TABS_RULES,
  splitOverflowTabs,
};
export type { OverflowTabItem, OverflowTabsProps, OverflowTabsVariant };
