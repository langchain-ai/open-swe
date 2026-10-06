"use client";

/*
 * Rules for SchemaCell.
 *
 * The rules themselves are `SCHEMA_CELL_RULES` below, not this comment.
 * `/design` renders that array verbatim beside the live pattern.
 *
 * A table cell is typed. The type, not the surface, chooses the chrome: a
 * single-select is one Quiet badge, a multi-select is compact badges that
 * cannot escape the column, money is mono, a day is a calendar day. Surfaces
 * map fields onto these cells; they do not invent a second reading.
 */

import { useLayoutEffect, useRef, useState, type ComponentProps, type ReactNode } from "react";
import type { Column } from "@tanstack/react-table";

import { DataGridColumnHeader } from "../data-grid/data-grid-column-header";
import { SiteMark } from "./site-mark";
import { Badge } from "../ui/badge";
import { Box, Inline, Stack } from "../ui/box";
import type { Glyph } from "../ui/glyphs";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "../ui/hover-card";
import { Icon } from "../ui/icon";
import {
  ProviderLogo,
  type ProviderLogoId,
} from "../ui/provider-logos";

const SCHEMA_CELL_RULES: readonly string[] = [
  "A cell has a schema type, and the type picks the chrome. Surfaces do not restyle the same field two ways.",
  "Wire vocabulary stops at the adapter. A visible enum uses an explicit product label, with `humanizeToken` only as a bounded fallback; never render underscores, provider identifiers, or storage names. A metric label states whether the value is an amount, count, rate, or share so the user never has to infer the unit from formatting.",
  "Single-select (`enum`) is one Quiet badge. Tone may vary with the value; the geometry may not. A value glyph may sit in the badge; it does not replace the label. The badge keeps the start of the label and truncates the end inside the column rather than wrapping, overflowing, or clipping both sides. A group row identity uses this same enum grammar; `GroupRowContent` owns the chevron and the hugging count.",
  "Multi-select (`set`) is compact Quiet badges in one nowrap row. As many values as fit stay visible; the last visible value may truncate. Further values collapse to a +N remainder chip. Widening the column reveals more chips. Hover or click the cell to read every value as the same Quiet badges, wrapping if a name is long. A wrapping paragraph of names is not a cell.",
  "Text truncates. Money and counts are mono tabular. A date is the calendar day the upstream published, also mono. A link is compact, https only, and stops the row click. An external platform hop carries that platform's branded logo, never a generic external-link glyph.",
  "Absence is the reserved empty mark, never a zero, never N/A, never a blank.",
  "Do not duplicate one fact as a number, badge, and progress bar in the same cell or preview. Choose the reading that best supports the decision, and reserve a second encoding for a genuinely different comparison.",
  "Every column header carries a representative glyph plus the title. The glyph is decorative; the accessible name is the title.",
];

type BadgeTone = NonNullable<ComponentProps<typeof Badge>["tone"]>;

const EMPTY_MARK = "—";
const EMPTY_CLASS = "font-mono text-label text-line-strong";

const ENUM_BADGE_CLASS = "max-w-full min-w-0 shrink justify-start text-left";
const SET_BADGE_CLASS = "max-w-full min-w-0 shrink justify-start text-left";
const BADGE_LABEL_CLASS = "min-w-0 truncate";
const SET_PREVIEW_BADGE_CLASS =
  "h-auto max-w-full justify-start whitespace-normal py-0.5 text-left";
const SET_ROW_CLASS = "min-w-0 max-w-full overflow-hidden";
const SET_LEAD_CLASS = "min-w-0 overflow-hidden";
/** Same as `Inline gap="xs"`. */
const SET_GAP_PX = 4;
/** Narrowest truncated chip that is still a badge, not a sliver. */
const SET_MIN_TRUNCATED_PX = 48;
const TEXT_CELL_CLASS = "min-w-0 truncate text-label text-ink";
const MUTED_TEXT_CELL_CLASS = "min-w-0 truncate text-label text-ink-muted";
const MONEY_CELL_CLASS = "font-mono text-label text-ink tabular-nums";
const DATE_CELL_CLASS =
  "whitespace-nowrap font-mono text-label text-ink tabular-nums";
const LINK_CELL_CLASS = "inline-flex min-w-0 items-center gap-1 text-meta text-info";

function AbsentValue() {
  return (
    <Box
      render={<span />}
      data-testid="absent-value"
      className={EMPTY_CLASS}
    >
      {EMPTY_MARK}
    </Box>
  );
}

function SchemaHeader<TData, TValue>({
  column,
  icon,
  title,
}: {
  column: Column<TData, TValue>;
  icon: Glyph;
  title: string;
}) {
  return (
    <DataGridColumnHeader
      column={column}
      title={title}
      icon={<Icon icon={icon} size="sm" />}
    />
  );
}

function SchemaText({
  muted = false,
  value,
}: {
  muted?: boolean;
  value: string | null;
}) {
  if (value === null) return <AbsentValue />;
  return (
    <Box
      render={<span />}
      title={value}
      className={muted ? MUTED_TEXT_CELL_CLASS : TEXT_CELL_CLASS}
    >
      {value}
    </Box>
  );
}

function SchemaMoney({ value }: { value: string | null }) {
  if (value === null) return <AbsentValue />;
  return (
    <Box render={<span />} className={MONEY_CELL_CLASS}>
      {value}
    </Box>
  );
}

function SchemaCount({ value }: { value: string | null }) {
  if (value === null) return <AbsentValue />;
  return (
    <Box render={<span />} className={MONEY_CELL_CLASS}>
      {value}
    </Box>
  );
}

function SchemaDate({ value }: { value: string | null }) {
  if (value === null) return <AbsentValue />;
  return (
    <Box render={<span />} className={DATE_CELL_CLASS}>
      {value}
    </Box>
  );
}

function SchemaEnum({
  icon,
  tone = "neutral",
  value,
  wrap = false,
}: {
  icon?: Glyph;
  tone?: BadgeTone;
  value: string | null;
  wrap?: boolean;
}) {
  if (value === null) return <AbsentValue />;
  return (
    <Box
      render={<span />}
      className={wrap ? "min-w-0 max-w-full" : SET_LEAD_CLASS}
    >
      <Badge
        tier="quiet"
        tone={tone}
        title={value}
        className={wrap ? SET_PREVIEW_BADGE_CLASS : ENUM_BADGE_CLASS}
      >
        {icon === undefined ? null : <Icon icon={icon} size="sm" />}
        <Box render={<span />} className={wrap ? undefined : BADGE_LABEL_CLASS}>
          {value}
        </Box>
      </Badge>
    </Box>
  );
}

/**
 * How many set chips fit in `available`, using measured full widths.
 *
 * Full chips that fit stay untruncated. One more chip may enter truncated
 * when leftover room is at least `minTruncated`. The rest collapse to a
 * remainder whose width is `remainderWidth`.
 */
function splitSchemaSet(
  widths: readonly number[],
  available: number,
  remainderWidth: number,
  gap: number,
  minTruncated: number = SET_MIN_TRUNCATED_PX
): { visible: number; lastTruncates: boolean } {
  const total = widths.length;
  if (total === 0) return { visible: 0, lastTruncates: false };

  const sum = (count: number, withRemainder: boolean) => {
    let width = 0;
    for (let index = 0; index < count; index += 1) {
      width += (widths[index] ?? 0) + (index > 0 ? gap : 0);
    }
    if (withRemainder) {
      width += (count > 0 ? gap : 0) + remainderWidth;
    }
    return width;
  };

  if (sum(total, false) <= available) {
    return { visible: total, lastTruncates: false };
  }

  let full = 0;
  while (full < total && sum(full + 1, full + 1 < total) <= available) {
    full += 1;
  }

  if (full < total) {
    const remainderAfter = full + 1 < total;
    const extra =
      (full > 0 ? gap : 0) +
      minTruncated +
      (remainderAfter ? gap + remainderWidth : 0);
    if (sum(full, false) + extra <= available) {
      return { visible: full + 1, lastTruncates: true };
    }
  }

  if (full === 0) return { visible: 1, lastTruncates: true };
  return { visible: full, lastTruncates: false };
}

function NonEmptySchemaSet({ values }: { values: readonly string[] }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const measureRef = useRef<HTMLDivElement>(null);
  const valueKey = values.join("\u001f");
  const [open, setOpen] = useState(false);
  const [split, setSplit] = useState(() =>
    values.length === 1
      ? { visible: 1, lastTruncates: false }
      : { visible: 1, lastTruncates: true }
  );

  useLayoutEffect(() => {
    const root = rootRef.current;
    const measure = measureRef.current;
    if (!root || !measure) return;

    const recompute = () => {
      const chipEls = [
        ...measure.querySelectorAll<HTMLElement>("[data-measure-chip]"),
      ];
      const widths = chipEls.map((el) => el.getBoundingClientRect().width);
      const remainderEl = measure.querySelector<HTMLElement>(
        "[data-measure-remainder]"
      );
      const remainderWidth = remainderEl?.getBoundingClientRect().width ?? 28;
      const available = root.clientWidth;

      /*
       * jsdom reports 0×0. Keep the compact first-chip + remainder reading
       * rather than flashing every name, then let a real layout re-run.
       */
      const next =
        available === 0 || widths.every((width) => width === 0)
          ? values.length === 1
            ? { visible: 1, lastTruncates: false }
            : { visible: 1, lastTruncates: true }
          : splitSchemaSet(
              widths,
              available,
              remainderWidth,
              SET_GAP_PX
            );

      setSplit((prev) =>
        prev.visible === next.visible && prev.lastTruncates === next.lastTruncates
          ? prev
          : next
      );
    };

    recompute();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(recompute);
    observer.observe(root);
    return () => observer.disconnect();
  }, [valueKey, values]);

  const visibleValues = values.slice(0, split.visible);
  const hidden = values.length - split.visible;

  return (
    <div ref={rootRef} className="relative min-w-0 w-full max-w-full">
      <div
        ref={measureRef}
        aria-hidden
        className="pointer-events-none invisible absolute top-0 left-0 -z-10 flex items-center gap-1"
      >
        {values.map((value, index) => (
          <Badge
            key={`measure-${index}-${value}`}
            data-measure-chip=""
            tier="quiet"
            tone="neutral"
            className="shrink-0"
          >
            {value}
          </Badge>
        ))}
        <Badge
          data-measure-remainder=""
          tier="quiet"
          tone="neutral"
          className="shrink-0"
        >
          {`+${Math.max(values.length - 1, 1)}`}
        </Badge>
      </div>
      <HoverCard open={open} onOpenChange={setOpen}>
        <HoverCardTrigger
          render={<button type="button" />}
          aria-label={values.join(", ")}
          className="inline-flex min-w-0 w-full max-w-full cursor-pointer rounded-compact text-left outline-none focus-visible:ring-2 focus-visible:ring-primary"
          onClick={(event) => {
            event.stopPropagation();
            setOpen(true);
          }}
        >
          <Inline gap="xs" align="center" className={SET_ROW_CLASS}>
            {visibleValues.map((value, index) => {
              const last = index === visibleValues.length - 1;
              const truncates = last && split.lastTruncates;
              return (
                <Box
                  key={`${index}-${value}`}
                  render={<span />}
                  className={truncates ? SET_LEAD_CLASS : "shrink-0"}
                >
                  <Badge
                    tier="quiet"
                    tone="neutral"
                    title={value}
                    className={truncates ? SET_BADGE_CLASS : "shrink-0"}
                  >
                    {truncates ? (
                      <Box render={<span />} className={BADGE_LABEL_CLASS}>
                        {value}
                      </Box>
                    ) : (
                      value
                    )}
                  </Badge>
                </Box>
              );
            })}
            {hidden === 0 ? null : (
              <Badge
                tier="quiet"
                tone="neutral"
                title={values.slice(split.visible).join(", ")}
                className="shrink-0"
              >
                {`+${hidden}`}
              </Badge>
            )}
          </Inline>
        </HoverCardTrigger>
        <HoverCardContent side="bottom" align="start" sideOffset={6}>
          <Stack gap="xs" data-testid="schema-set-preview">
            {values.map((value, index) => (
              <SchemaEnum key={`${index}-${value}`} value={value} wrap />
            ))}
          </Stack>
        </HoverCardContent>
      </HoverCard>
    </div>
  );
}

function SchemaSet({ values }: { values: readonly string[] }) {
  if (values.length === 0) return <AbsentValue />;
  return <NonEmptySchemaSet values={values} />;
}

function SchemaLink({
  href,
  icon,
  label,
  provider,
}: {
  href: string | null;
  icon?: Glyph;
  label: string;
  provider?: ProviderLogoId;
}) {
  if (href === null) return <AbsentValue />;
  return (
    <Box
      render={
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(event) => event.stopPropagation()}
        />
      }
      data-provider={provider}
      className={LINK_CELL_CLASS}
    >
      {provider === undefined ? (
        icon === undefined ? (
          <SiteMark href={href} />
        ) : (
          <Icon icon={icon} size="sm" />
        )
      ) : (
        <ProviderLogo provider={provider} className="size-3.5" />
      )}
      {label}
    </Box>
  );
}

function SchemaCellPreview({ children }: { children: ReactNode }) {
  return (
    <Inline gap="md" wrap align="center">
      {children}
    </Inline>
  );
}

export {
  SCHEMA_CELL_RULES,
  SchemaCount,
  SchemaDate,
  SchemaEnum,
  SchemaHeader,
  SchemaLink,
  SchemaMoney,
  SchemaSet,
  SchemaText,
  SchemaCellPreview,
  splitSchemaSet,
};
export type { BadgeTone };
