"use client";

/*
 * Rules for PageBand.
 *
 * The rules themselves are `PAGE_BAND_RULES` below, not this comment.
 * `/design` renders that array verbatim beside the live pattern, so the band a
 * builder reads in the gallery and the band this file draws are the same
 * strings.
 *
 * Geometry is measured, not guessed. Every number here comes from the lineage
 * band on CORE 03 (Paper file 01KYQDVRVBZ6KYPDP179BS2M3G, node NWG-0): 44px
 * tall, 12px inset, 8px gap, the muted fill, one hairline underneath. CORE 07
 * (QKS-0) is where the band's job is decided; this file is where its shape is.
 *
 * What the code enforces: one height, one inset, one fill, and exactly two
 * variants. What the rules state: that a second band is never the answer, and
 * that a band on an object route exists for one reason only.
 */

import type { ReactNode } from "react";

import { HostLink } from "../host";
import { Box, Inline } from "../ui/box";
import { Button } from "../ui/button";
import { ArrowLeft, ChevronRight } from "../ui/glyphs";
import { Icon } from "../ui/icon";

const PAGE_BAND_RULES: readonly string[] = [
  "Chrome may only assert what the content cannot: where you are (the sidebar), how deep you are (a lineage band), what you can do (the page toolbar). Zero, one, or one. Never two.",
  "The band is always 44px at the same y, so the content edge never jumps between pages. That is why the height is not a prop: a band that measured itself from its contents would move the first row of every page it sits on.",
  "There is never a second band. A global header stacked on a page toolbar says the same thing twice, and none of the nine boards stack. Filters fuse into whichever band already exists rather than earning a strip of their own.",
  "On an object route the band exists because the sidebar cannot express depth. That is its only job, which is why the lineage variant carries exactly three things: the way back, the lineage itself, and the one action that belongs to the object you are looking at.",
  "The toolbar variant is verbs fused with filters, in that order, left to right, at the control rung. Placement picks the size and this row is a toolbar, so everything standing in it is 32px, never 28px. Agent Focus may pass `edge=\"none\"` so the band sits flush on the transcript: no bottom hairline and `bg-shell` (same fill as the work column) — never `bg-muted`, which left a colour seam.",
  "Command search is not band furniture. It moves to Cmd-K only: a field reached by shortcut should not park 320px of chrome on every page. A search that filters the rows below it is a page filter and belongs in the toolbar variant; a search that navigates the product is Cmd-K.",
  "Reading surfaces take no band at all. A masthead is content, not chrome, and an edition that wanted a toolbar wanted to be a queue instead.",
  "The band renders its own back affordance so every object route reaches back the same way; the caller supplies where back goes, never what it looks like.",
];

/*
 * NWG-0: height 44, paddingInline 12, gap 8, fill --oct-surface-muted, 1px
 * bottom hairline on --oct-border. `h-toolbar` is the 44px rung of the density
 * ladder, which exists for exactly this band.
 */
const BAND_CLASS =
  "h-toolbar w-full shrink-0 border-b border-line px-3 text-label";
/** Focus thread column — same fill as the shell card, no hairline. */
const BAND_FLUSH_CLASS =
  "h-toolbar w-full shrink-0 border-b-0 bg-shell px-3 text-label";

interface LineageCrumb {
  /** Shown verbatim. The last crumb is the object you are on. */
  label: string;
  /** Present on every crumb but the last one, which is where you already are. */
  href?: string;
}

/** One crumb, plus the chevron that precedes it from the second crumb on. */
function Crumb({
  crumb,
  first,
  last,
}: {
  crumb: LineageCrumb;
  first: boolean;
  last: boolean;
}) {
  return (
    <Inline render={<li />} gap="sm" align="center" className="min-w-0">
      {first ? null : (
        <Icon icon={ChevronRight} size="sm" className="text-ink-subtle" />
      )}
      {crumb.href === undefined || last ? (
        <Box
          render={<span />}
          className={last ? "truncate font-medium text-ink" : "text-ink-muted"}
        >
          {crumb.label}
        </Box>
      ) : (
        <Box
          render={<HostLink href={crumb.href} />}
          className="text-ink-muted transition-colors duration-fast ease-out-quint hover:text-ink motion-reduce:transition-none"
        >
          {crumb.label}
        </Box>
      )}
    </Inline>
  );
}

interface PageBandLineageProps {
  variant: "lineage";
  /** Root first; the last entry is the object the route is on. */
  lineage: readonly LineageCrumb[];
  /** Where back goes. Omit only when the route genuinely has no parent. */
  onBack?: () => void;
  /** Spoken by the back control; name the destination, not the direction. */
  backLabel?: string;
  /** The one action that belongs to this object. Not a toolbar. */
  action?: ReactNode;
}

interface PageBandToolbarProps {
  variant: "toolbar";
  /** Verbs fused with filters, left to right, all at the control rung. */
  children: ReactNode;
  /**
   * Drop the bottom hairline and match the shell column fill (Agent Focus —
   * band sits flush on the transcript). Default keeps muted + seam for list
   * toolbars.
   */
  edge?: "line" | "none";
}

type PageBandProps = PageBandLineageProps | PageBandToolbarProps;

/**
 * The one 44px chrome band: lineage on object routes, toolbar on list routes,
 * and never both.
 */
function PageBand(props: PageBandProps) {
  if (props.variant === "toolbar") {
    const flush = props.edge === "none";
    return (
      <Inline
        data-slot="page-band"
        data-variant="toolbar"
        data-edge={props.edge ?? "line"}
        bg={flush ? "none" : "muted"}
        gap="sm"
        align="center"
        className={flush ? BAND_FLUSH_CLASS : BAND_CLASS}
      >
        {props.children}
      </Inline>
    );
  }

  const { action, backLabel = "Back", lineage, onBack } = props;

  return (
    <Inline
      data-slot="page-band"
      data-variant="lineage"
      bg="muted"
      gap="sm"
      align="center"
      className={BAND_CLASS}
    >
      {onBack === undefined ? null : (
        <Button variant="ghost" size="icon-sm" onClick={onBack}>
          <Icon icon={ArrowLeft} />
          <Box render={<span />} className="sr-only">
            {backLabel}
          </Box>
        </Button>
      )}

      <Inline
        render={<nav aria-label="Lineage" />}
        grow
        align="center"
        className="min-w-0"
      >
        <Inline render={<ol />} gap="sm" align="center" className="min-w-0">
          {lineage.map((crumb, index) => (
            <Crumb
              key={crumb.label}
              crumb={crumb}
              first={index === 0}
              last={index === lineage.length - 1}
            />
          ))}
        </Inline>
      </Inline>

      {action === undefined ? null : (
        <Inline gap="sm" align="center" className="shrink-0">
          {action}
        </Inline>
      )}
    </Inline>
  );
}

export { PageBand, PAGE_BAND_RULES };
export type {
  LineageCrumb,
  PageBandLineageProps,
  PageBandProps,
  PageBandToolbarProps,
};
