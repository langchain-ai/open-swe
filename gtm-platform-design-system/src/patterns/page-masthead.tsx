"use client";

/*
 * Rules for PageMasthead.
 *
 * The rules themselves are `PAGE_MASTHEAD_RULES` below, not this comment.
 * `/design` renders that array verbatim beside the live pattern.
 *
 * This is the title strip PageFrame's header already draws, minus the column.
 * PageFrame welds its header to `PAGE_COLUMN_CLASS`, so a work or wide route
 * could not borrow the page title without also taking the 768 cap, and Home
 * answered that by hand-rolling a local strip inside its archetype file. A
 * hand-rolled title is exactly the drift the patterns tier exists to stop, so
 * the strip moves here and the three questions it was answering by accident
 * get answered on purpose: where counts live, which type rung the surface is
 * spending, and what happens to the 26px editorial title.
 *
 * What the code enforces: one h1 on the page rung, one description two rungs
 * under it, and counts that can only be Quiet badges. What the rules state:
 * why the masthead has no verbs, and why 26px is a decision to file rather
 * than a rung to add.
 */

import { HostLink as Link } from "../host";

import { Badge } from "../ui/badge";
import { Box, Inline, Stack } from "../ui/box";
import type { Glyph } from "../ui/glyphs";
import { Icon } from "../ui/icon";
import {
  ProviderLogo,
  type ProviderLogoId,
} from "../ui/provider-logos";

const PAGE_MASTHEAD_RULES: readonly string[] = [
  "A masthead is content, not chrome. It states what today is and what is in it; PageBand states where you are and what you can do. That is why Home mounts a masthead and no band, and why a surface that already carries a lineage band does not also get a masthead: the two would say the page's name twice, which is the same failure as a second band.",
  "Counts ride in the masthead, never in the band. A count is a reading of the page's contents and belongs under the sentence that describes them, on their own line after the title and the description; the band is chrome and chrome may only assert location, depth, and verbs (`PAGE_BAND_RULES`). Counts arrive as data, not as children, so a call site cannot smuggle a control into the lane. They sit under the title rather than trailing it because the title row is the one lane a surface may also give a period or scope control, and a reading and a control competing for the same line is how that row wraps.",
  "A count with an href is a router, not a verb. It opens the owning surface the same way a Home queue row does (platform 1.2), which is how a number stops being decoration. A glyph or a provider mark may ride in the Quiet badge; both arrive as data on the count. A count without an href stays a reading. Do not invent a destination, and do not promote the lane into KPI tiles: a number is a row or a cell, never a card (`STAT_READOUT_RULES`).",
  "Counts are Quiet badges and there is no tier prop. One Urgent per region is already the badge law, and a masthead sits above a queue that will legitimately want the region's Urgent for the one thing that is actually urgent. A count is orientation, so it never spends that budget.",
  "`text-page` is the surface's one borrowed rung. Wiki 02: a surface spends two sizes and borrows a third once. The masthead is where Home spends the borrow, which is what forbids a second `text-page` element anywhere else on the same route.",
  "The description sits two rungs below the title: `text-page` pairs with `text-body`, exactly as PageFrame's header pairs them. One line. A description that needs a second line wanted to be a section.",
  "The masthead has no actions slot, and that is the decision, not an omission. Verbs belong to the band on object routes and to the section that owns them everywhere else; a title strip that grows a primary button becomes the action lane Home already deleted (`surface-decisions`, platform 1.2). A count-as-link is not that slot.",
  "A named Agent feature may pass a leading glyph on the title row (`Icon` `lg`, same construction as `ListSidebarTitle`). It names the feature. It is not a verb and it does not replace the h1.",
  "The masthead takes no width prop, for the same reason PageFrame does not: measure belongs to AppShell (`APP_SHELL_RULES`). The strip fills whatever the route was given, which is the whole point of extracting it from a frame that is welded to the 768 reading column.",
  "OPEN, FILED: the boards draw the editorial title at 26px, which is above the five-pair ladder and below nothing that could carry it. It ships on `text-page` (20px) until a designer rules, because adding a sixth rung needs a decision and two consumers in the same PR. Do not set 26px locally — that is the type-pair law's exact failure mode. (annex 06, Home.)",
];

/** Counts are orientation, so the tones stop short of `risk`. */
type PageMastheadCountTone = "neutral" | "info" | "attention";

interface PageMastheadCount {
  /** Stable key. The count's noun, never its position. */
  id: string;
  /** Read as a unit, pre-formatted: "13 in inbox", never a bare "13". */
  label: string;
  tone?: PageMastheadCountTone;
  /** Owning surface. When set, the count is a router. */
  href?: string;
  /** Product glyph. Ignored when `provider` is set: a named channel is a brand mark. */
  icon?: Glyph;
  /** Channel brand mark, native colour, sized to the badge. */
  provider?: ProviderLogoId;
}

interface PageMastheadProps {
  /** The page's one `text-page` element. Nothing else on the route may take it. */
  title: string;
  /** One line, two rungs down. Longer is a section. */
  description?: string;
  /** Feature glyph on the title row. Orientation, not a verb. */
  icon?: Glyph;
  /** Readings of the page's contents. Rendered Quiet, never Urgent. */
  counts?: readonly PageMastheadCount[];
}

/** Keyboard focus on a count-as-link. Pressable ring, not the field family. */
const COUNT_LINK_CLASS =
  "cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-primary";

/** The mark that names what the count is counting, sized to the 20px badge. */
function CountMark({ count }: { count: PageMastheadCount }) {
  if (count.provider !== undefined) {
    return <ProviderLogo provider={count.provider} className="size-3" />;
  }
  if (count.icon === undefined) return null;
  return <Icon icon={count.icon} size="sm" className="size-3" />;
}

/** One Quiet reading, optionally a router to the surface that owns the number. */
function MastheadCount({ count }: { count: PageMastheadCount }) {
  const href = count.href;
  return (
    <Badge
      tier="quiet"
      tone={count.tone ?? "neutral"}
      data-count={count.id}
      render={href === undefined ? undefined : <Link href={href} />}
      className={href === undefined ? undefined : COUNT_LINK_CLASS}
    >
      <CountMark count={count} />
      {count.label}
    </Badge>
  );
}

/**
 * The page title strip for routes that are not a PageFrame: an h1 on the page
 * rung, one description under it, and Quiet counts trailing.
 */
function PageMasthead({ counts, description, icon, title }: PageMastheadProps) {
  return (
    <Inline
      data-slot="page-masthead"
      render={<header />}
      gap="md"
      align="start"
      justify="between"
      wrap
    >
      <Stack gap="sm" className="min-w-0">
        {/*
         * The title and its sentence belong together, a rung apart. The facts are a separate band
         * under both, a step further out, so a row of badges never reads as part of the heading.
         */}
        <Stack gap="xs" className="min-w-0">
          <Inline gap="sm" align="center" className="min-w-0">
            {icon === undefined ? null : (
              <Icon icon={icon} size="lg" className="shrink-0 text-ink" />
            )}
            <Box
              render={<h1 />}
              className="truncate text-page font-semibold tracking-tightish text-ink"
            >
              {title}
            </Box>
          </Inline>
          {description === undefined ? null : (
            <Box render={<p />} className="text-pretty text-body text-ink-subtle">
              {description}
            </Box>
          )}
        </Stack>
        {counts === undefined || counts.length === 0 ? null : (
          <Inline data-slot="page-masthead-counts" gap="sm" align="center" wrap className="min-w-0">
            {counts.map((count) => (
              <MastheadCount key={count.id} count={count} />
            ))}
          </Inline>
        )}
      </Stack>
    </Inline>
  );
}

export { PageMasthead, PAGE_MASTHEAD_RULES };
export type { PageMastheadCount, PageMastheadCountTone, PageMastheadProps };
