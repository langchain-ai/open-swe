"use client";

/*
 * Rules for GroupRowContent.
 *
 * The rules themselves are `GROUP_ROW_RULES` below, not this comment.
 * `/design` table-grouping restates the count law; this file owns the
 * geometry every table and split-list group row must share.
 */

import type { CSSProperties, ReactNode } from "react";

import { Badge } from "./badge";
import { Box, Inline } from "./box";
import { ChevronDown } from "./glyphs";
import { Icon } from "./icon";

const GROUP_ROW_RULES: readonly string[] = [
  "A group row is a row in the same grid, not a heading outside it. The anatomy is fixed: disclosure chevron, then the group identity, then the compact count.",
  "The identity is that field's SchemaCell when the surface provides one. A bare string still becomes one Quiet badge. Do not set the group name in font-medium body type.",
  "The count sits immediately after the identity. It is mono tabular meta, not a badge, and it does not travel to the trailing edge of the first column. A wide pinned name column is not a reason to stretch the label.",
  "Nested groups indent the chevron. The count stays on the identity cluster at every depth.",
];

const GROUP_COUNT_CLASS =
  "shrink-0 font-mono text-meta text-ink-subtle tabular-nums";
const GROUP_LABEL_BADGE_CLASS =
  "max-w-full min-w-0 shrink justify-start text-left";

interface GroupRowContentProps {
  count: number;
  depth?: number;
  expanded: boolean;
  label: ReactNode;
  onToggle: () => void;
  compact?: boolean;
}

function GroupRowLabel({ label }: { label: ReactNode }) {
  if (typeof label !== "string") return label;
  return (
    <Badge
      tier="quiet"
      tone="neutral"
      title={label}
      className={GROUP_LABEL_BADGE_CLASS}
    >
      {label}
    </Badge>
  );
}

/** Shared content geometry for local, server-backed, table, and split-list group rows. */
function GroupRowContent({
  compact = false,
  count,
  depth = 0,
  expanded,
  label,
  onToggle,
}: GroupRowContentProps) {
  const indentStyle = {
    "--group-row-padding": `${depth * 20}px`,
  } as CSSProperties;

  return (
    <Inline
      gap="sm"
      align="center"
      data-slot="group-row-content"
      className="min-w-0"
    >
      <Box
        render={<span />}
        style={indentStyle}
        className="inline-flex shrink-0 items-center ps-(--group-row-padding)"
      >
        <Box
          render={<button type="button" />}
          aria-expanded={expanded}
          aria-label={`${expanded ? "Collapse" : "Expand"} group`}
          onClick={(event) => {
            event.stopPropagation();
            onToggle();
          }}
          className={`${compact ? "size-badge" : "size-control-sm"} inline-flex items-center justify-center rounded-control text-ink-subtle transition-[scale,color] duration-fast ease-out-quint hover:text-ink active:scale-[0.97] motion-reduce:transition-none`}
        >
          <Icon
            icon={ChevronDown}
            size="sm"
            className="transition-transform duration-fast ease-out-quint in-aria-[expanded=false]:-rotate-90 motion-reduce:transition-none rtl:in-aria-[expanded=false]:rotate-90"
          />
        </Box>
      </Box>
      <Box data-slot="group-row-label" className="min-w-0 truncate">
        <GroupRowLabel label={label} />
      </Box>
      <Box
        render={<span />}
        data-slot="group-row-count"
        className={GROUP_COUNT_CLASS}
      >
        {count}
      </Box>
    </Inline>
  );
}

export { GROUP_ROW_RULES, GroupRowContent };
export type { GroupRowContentProps };
