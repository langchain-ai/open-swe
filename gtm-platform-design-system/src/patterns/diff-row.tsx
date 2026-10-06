"use client";

/*
 * Rules for DiffRow / ChangeSet / ChangeSetSection.
 *
 * Structured before→after evidence for its two consumers: a play version
 * compared to live (VersionedEditor's diff slot) and a proposed or completed
 * write (Receipt). ChangeSet is one quiet subcard; sections group related
 * fields; DiffRow is one mutation as label + before chip → after chip. No
 * Frame-in-Frame, no action lane, no sparklines.
 */

import type { ReactNode } from "react";

import { Box, Inline, Stack } from "../ui/box";
import { cn } from "../ui/cn";

const DIFF_ROW_RULES: readonly string[] = [
  "DiffRow shows one field mutation: caps label · before → after. The default is chips (muted line-through before, ink after). `layout=\"block\"` is the same evidence for multiline markdown: stacked pre-wrapped mono blocks, never a second side-by-side editor. Empty before reads as '—'.",
  "ChangeSet has exactly two consumers and both are shipped: a play version compared to live (`VersionedEditor`'s `diff` slot) and a proposed or completed write (`Receipt`). Every before-and-after in this product is one of those two. When a third surface needs one, grow this pattern — never a second diff vocabulary.",
  "Compose many DiffRows into a ChangeSet — one quiet subcard (`border-line` + `rounded-control`), never a second Frame. Nesting Frame inside Receipt is how double mats come back.",
  "Related fields share a ChangeSetSection (caps subsection header over its rows). Flat ChangeSets stay flat; do not invent a section for a single orphan row.",
  "Use mono for values (identifiers, amounts, stages). Never sparklines, KPI tiles, or an action lane — the host owns the verb, and the two hosts own different ones: the Receipt's is already done, the PublishGate's is Publish. DiffRow is evidence of what changed, never where you change it.",
];

/** Quiet chip for a before/after value — shared so both sides align. */
const VALUE_CHIP_CLASS =
  "min-w-0 max-w-full truncate rounded-compact px-2 py-0.5 font-mono tabular-nums";

interface DiffRowProps {
  label: string;
  before?: ReactNode;
  after: ReactNode;
  layout?: "chip" | "block";
  className?: string;
}

function DiffRow({
  after,
  before,
  className,
  label,
  layout = "chip",
}: DiffRowProps) {
  if (layout === "block") {
    return (
      <Stack
        data-slot="diff-row"
        data-testid="diff-row"
        gap="sm"
        className={cn(
          "w-full border-b border-line px-3 py-2 last:border-b-0",
          className
        )}
      >
        <Box
          render={<span />}
          className="text-meta font-medium tracking-caps text-ink-subtle uppercase"
        >
          {label}
        </Box>
        <Box
          render={<pre />}
          className="whitespace-pre-wrap font-mono text-meta text-ink-subtle line-through"
        >
          {before ?? "—"}
        </Box>
        <Box
          render={<span />}
          aria-hidden
          className="text-meta text-ink-subtle"
        >
          →
        </Box>
        <Box
          render={<pre />}
          className="whitespace-pre-wrap font-mono text-label font-medium text-ink"
        >
          {after}
        </Box>
      </Stack>
    );
  }

  return (
    <Inline
      data-slot="diff-row"
      data-testid="diff-row"
      gap="md"
      align="center"
      className={cn(
        "min-h-row-data w-full border-b border-line px-3 py-2 last:border-b-0",
        className
      )}
    >
      <Box
        render={<span />}
        className="w-24 shrink-0 text-meta font-medium tracking-caps text-ink-subtle uppercase"
      >
        {label}
      </Box>
      <Inline gap="sm" align="center" className="min-w-0 flex-1">
        <Box
          render={<span />}
          className={cn(
            VALUE_CHIP_CLASS,
            "bg-muted text-meta text-ink-subtle line-through"
          )}
        >
          {before ?? "—"}
        </Box>
        <Box
          render={<span />}
          aria-hidden
          className="shrink-0 text-meta text-ink-subtle"
        >
          →
        </Box>
        <Box
          render={<span />}
          className={cn(
            VALUE_CHIP_CLASS,
            "border border-line bg-canvas text-label font-medium text-ink"
          )}
        >
          {after}
        </Box>
      </Inline>
    </Inline>
  );
}

interface ChangeSetSectionProps {
  /** Caps subsection label, e.g. "Opportunity" or "Timing". */
  title: string;
  children: ReactNode;
  className?: string;
}

function ChangeSetSection({
  children,
  className,
  title,
}: ChangeSetSectionProps) {
  return (
    <Stack
      data-slot="change-set-section"
      gap="none"
      className={cn(
        "w-full border-t border-line first:border-t-0",
        className
      )}
    >
      <Box
        render={<h4 />}
        className="border-b border-line bg-muted px-3 py-1.5 text-meta font-medium tracking-caps text-ink-subtle uppercase"
      >
        {title}
      </Box>
      <Stack gap="none">{children}</Stack>
    </Stack>
  );
}

interface ChangeSetProps {
  /** Optional card label above the rows, e.g. "Changes". */
  title?: string;
  children: ReactNode;
  className?: string;
}

/**
 * One quiet subcard of field mutations. Prefer ChangeSetSection children when
 * fields cluster; otherwise DiffRow children sit in a single list.
 */
function ChangeSet({ children, className, title }: ChangeSetProps) {
  return (
    <Stack
      data-slot="change-set"
      data-testid="change-set"
      gap="none"
      border="line"
      radius="control"
      bg="panel"
      className={cn("w-full overflow-hidden", className)}
    >
      {title ? (
        <Box
          render={<h3 />}
          className="border-b border-line bg-muted px-3 py-2 text-meta font-medium text-ink"
        >
          {title}
        </Box>
      ) : null}
      <Stack gap="none">{children}</Stack>
    </Stack>
  );
}

export { ChangeSet, ChangeSetSection, DiffRow, DIFF_ROW_RULES };
export type { ChangeSetProps, ChangeSetSectionProps, DiffRowProps };
