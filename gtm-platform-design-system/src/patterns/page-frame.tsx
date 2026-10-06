"use client";

/*
 * Rules for PageFrame.
 *
 * The rules themselves are `PAGE_FRAME_RULES` below, not this comment.
 * `/design` renders that array verbatim beside the live pattern, so the shape
 * a builder reads in the gallery and the shape this file imposes are the same
 * strings.
 *
 * This is the template tier: the frame fixes what code can fix. The column
 * width, the header, the gap between sections and the position of the danger
 * zone are structural here, which is why `dangerZone` is a slot rather than a
 * convention. A misplaced danger section is not a lint finding in this system,
 * it is unsayable.
 *
 * PageSection carries a second, narrower decision and so has its own array,
 * `PAGE_SECTION_RULES`: whether a section is a panel or sits flat on the
 * canvas. `contained` is the only way to reach the panel treatment, so the
 * commitment ledger in those rules is a question every section has to answer
 * rather than a class a call site can quietly add.
 *
 * ROOT PAD LAW (2026-08-10): AppShell owns the horizontal gutter. PageFrame
 * and page stacks own vertical rhythm only. Contained sections default to
 * flush bodies so row `px-3` is not stacked on panel `p-4`.
 */

import { useId } from "react";
import type { ReactNode } from "react";

import { Box, Inline, Stack } from "../ui/box";
import { cn } from "../ui/cn";
import { AlertTriangle, type Glyph } from "../ui/glyphs";
import { Icon } from "../ui/icon";

const PAGE_FRAME_RULES: readonly string[] = [
  "A page is one readable column, 768px wide, centred in whatever the shell gives it. That is `max-w-3xl` off the stock scale, chosen because it is the widest step where a label and description on the left and their control on the right still read as one row rather than two columns the eye has to travel between. Wider is a table surface, which is a different family and gets its own frame when that decision is made.",
  "This column is the width law's reading measure. 768 = `max-w-3xl` = `--gtm-container-reading` = AppShell's `contentWidth='reading'`: one number reached three ways, deliberately, so a PageFrame route inherits the cap once rather than fighting a second one. The number is unchanged by the width law (Primer's `medium` 768, Tailwind's `max-w-3xl`); what the law added is the other three measures, which live above this frame. (`APP_SHELL_RULES`; `docs/plan/gtm-agent-product/width-padding-evidence.md` section 5.3 rule 3.)",
  "Width above the frame belongs to AppShell, and the frame takes no width prop. A route that needs a different measure asks the shell for one of the four named values (reading / work / wide / interstitial); a surface wider than this column is a different page family, not a wider PageFrame. The shell contributes the route gutter (`px-4 lg:px-6`). PageFrame adds vertical rhythm only (`PAGE_STACK_CLASS` / `py-6`) — never a second full `padding=\"xl\"` / `p-6` inside the measure.",
  "The header is the title on the page step plus, at most, a one line description on ink-subtle. A description that needs a second line is a section, not a header.",
  "That header is welded to this column and cannot be borrowed. A work or wide route that needs a page title takes `PageMasthead`, which is the same strip without the 768 cap and with the counts lane; it never lifts these classes into an archetype file. Two components draw a page title, deliberately, and the width is what separates them: inside the reading column it is the frame's, outside it is the masthead's.",
  "Sections render in source order with one gap between them, top to bottom. No tabs, no accordions, no reordering by importance: the order the ticket lists them in is the order the page shows them in.",
  "If the page has a destructive or irreversible section it is last, always, and it arrives through the `dangerZone` slot rather than as another child. The frame renders it after everything else behind a line-strong seam with a risk toned heading, so putting it third from the top is not a thing this pattern can express.",
  "Whatever sits in the danger zone still owes its own confirmation: pair it with ConfirmableAction. The zone is where a dangerous control lives, not permission for it to fire on one click.",
  "Tabs are not a page organisation device in this system yet. Tabs versus stacked sections is on the open decisions list in wiki 02 ('Decisions, not components: the patterns tier'), which means a surface that wants them files the decision rather than shipping them.",
];

/*
 * The commitment ledger, from CORE 07 section 05 (Paper file
 * 01KYQDVRVBZ6KYPDP179BS2M3G, node QXT-0). `contained` is the only prop that
 * can express it, which is the point: a section cannot grow a border by adding
 * classes at a call site, it has to answer the question this ledger asks.
 */
const PAGE_SECTION_RULES: readonly string[] = [
  "Containment follows commitment. A panel is not decoration and not a section marker. It says: this set is bounded, and you act inside it. Where you orient rather than commit, a border lies about how the content behaves.",
  "Hierarchy follows the user's decision. One region leads the first viewport and supporting regions yield in width, density, or placement. Equal visual weight is not neutral when the jobs are unequal, and an endpoint schema is not permission to build an equal-weight card mosaic.",
  "Write the containment ledger before composing the section: every visible edge must name the bounded action region it protects. Shell, page, panel, and row each own one inset on one axis; a call site never compensates for an ancestor's padding. If two peer surfaces need the same correction, fix this pattern or the owning primitive before adding another class.",
  "The Overview ledger: Conversations and Meetings are peer contained panels because each is a bounded working set. Important updates is contained; New this week stays flat around richer object cards. Suggested play is a titled flat section whose one `ReviewBand` owns its edge. Do not hand-build that muted panel. The conditional critical alert owns one untitled panel.",
  "'Prepared for you' is a panel for prepared work you accept; 'Running quietly' is flat, because a status line is not work. Churn alerts are a panel for the open queue, a bounded set you triage, and flat for what already resolved. Weekly intelligence is flat throughout: an edition is a reading surface, and a border would frame the week.",
  "Opportunities take a panel for the stage matrix and for each proposed write, and stay flat for the list you scan. Meetings take one panel for the brief you act on; the day list is a pane, never a stack of cards.",
  "Every page that mixes commitment with orientation mixes treatments. The pages that only orient never take a border at all, and a page whose every section is contained has not made the distinction, it has skipped it.",
  "A panel is surface plus one hairline plus the panel radius, and that is the whole treatment. No shadow, no second fill, no accent edge. Hierarchy comes from density and colour, never from which section owns a box.",
  "Contained body defaults to `inset=\"flush\"` (edge-to-edge). Row lists and tables own `px-3`. Prose passes `inset=\"padded\"`. Never panel pad + row pad.",
  "`headerPlacement=\"in-panel\"` remains available for card chrome with the title under a hairline inside the panel. Product `/settings` keeps titles **outside** (`above`) and uses one feature card per preference (ReUI card-24). Home / queue keep the default `above` title outside the panel.",
  "The contained panel clips (`overflow-hidden`). Flush DecisionRow / ConversationRow hover fills are square; without the clip they poke past the panel radius on the first and last rows. Do not round each row to paper over a missing clip.",
  "Never nest a panel in a panel. ReUI's dashboard blocks are all contained, six Frame and two Card, none flat, but those are analytics surfaces of tiles and charts and ours is a work queue: the reference sets the ceiling, not the rule.",
  "Section actions affect the whole section. Filters, grouping, and search that affect a table stay in FilterableTable immediately above that grid; controls that affect a list stay in the list pane. Do not use the actions slot to move a local control into a more decorative header.",
  "A section title may carry a decorative glyph. The accessible name stays the title string. Do not invent a second heading treatment to hang an icon.",
];

/** The column: 768px on the stock scale, centred. Horizontal gutter is the shell's. */
const PAGE_COLUMN_CLASS = "mx-auto w-full max-w-3xl";

/**
 * Vertical page rhythm inside AppShell's measure. Shared by PageFrame and by
 * archetype stacks that compose sections without PageFrame. Never add a second
 * horizontal pad here — that was the Home cushion bug.
 */
const PAGE_STACK_CLASS = "py-6";

type PageSectionInset = "flush" | "padded";

/**
 * Where the section title sits when `contained`.
 * - `above` — title outside the panel (Home / queue ledger).
 * - `in-panel` — title inside a bordered card header (ReUI settings-7).
 */
type PageSectionHeaderPlacement = "above" | "in-panel";

/** Contained body: flush is the default so list hosts cannot reintroduce pad+row. */
const CONTAINED_BODY_CLASS: Record<PageSectionInset, string> = {
  flush: "p-0",
  padded: "p-4",
};

/** settings-7 card header: title + description under a bottom hairline. */
const PANEL_HEADER_CLASS = "gap-0.5 border-b border-line px-5 py-3";

/** The danger zone's seam. line-strong because it separates families, not rows. */
const DANGER_SEAM_CLASS = "border-t border-line-strong pt-6";

interface PageSectionProps {
  title: string;
  /** Decorative. The heading text stays the accessible name. */
  icon?: Glyph;
  /** One or two lines at most; longer belongs in the section's body. */
  description?: string;
  /** Section level controls, trailing the heading. */
  actions?: ReactNode;
  /**
   * A bounded set you act inside gets the panel treatment; a window onto
   * something continuous stays flat. See `PAGE_SECTION_RULES`.
   */
  contained?: boolean;
  /**
   * Contained body inset. Default `flush` (row lists / tables). Prose uses
   * `padded`. Ignored when not contained. When `headerPlacement="in-panel"`,
   * defaults to `flush` so rows own their pad (ReUI settings-7).
   */
  inset?: PageSectionInset;
  /**
   * @deprecated Use `inset`. `flush={false}` maps to `padded`; otherwise flush.
   * `inset` wins when both are set.
   */
  flush?: boolean;
  /**
   * Contained header placement. Default `above`. Settings cards use
   * `in-panel` so the title sits in the card chrome (settings-7).
   */
  headerPlacement?: PageSectionHeaderPlacement;
  /**
   * Anchor id for in-page nav (settings scoped rail). Lands on the section
   * element so scrollIntoView / hash jumps target the whole block.
   */
  id?: string;
  children: ReactNode;
}

/** One titled block inside a PageFrame. Sections stack; they never nest. */
function PageSection({
  title,
  icon,
  description,
  actions,
  contained = false,
  inset,
  flush,
  headerPlacement = "above",
  id,
  children,
}: PageSectionProps) {
  const headingId = useId();
  const inPanel = contained && headerPlacement === "in-panel";
  const bodyInset: PageSectionInset =
    inset ??
    (inPanel ? "flush" : flush === false ? "padded" : "flush");

  const titleBlock = (
    <Stack gap={inPanel ? "none" : "xs"} className={inPanel ? PANEL_HEADER_CLASS : undefined}>
      <Inline gap="lg" justify="between" align="start" wrap>
        <Inline gap="sm" align="center">
          {icon === undefined ? null : (
            <Icon icon={icon} size="sm" className="text-ink-subtle" />
          )}
          <Box
            render={<h2 id={headingId} />}
            className="text-title font-medium text-ink"
          >
            {title}
          </Box>
        </Inline>
        {actions === undefined ? null : (
          <Inline gap="sm" align="center">
            {actions}
          </Inline>
        )}
      </Inline>
      {description === undefined ? null : (
        <Box
          render={<p />}
          className={
            inPanel
              ? "text-meta text-ink-subtle"
              : "text-label text-ink-subtle"
          }
        >
          {description}
        </Box>
      )}
    </Stack>
  );

  return (
    <Stack
      id={id}
      data-slot="page-section"
      data-contained={contained}
      data-inset={contained ? bodyInset : undefined}
      data-header={contained ? headerPlacement : undefined}
      render={<section aria-labelledby={headingId} />}
      gap={inPanel ? "none" : "lg"}
      className={id === undefined ? undefined : "scroll-mt-6"}
    >
      {inPanel ? null : titleBlock}
      {contained ? (
        inPanel ? (
          <Stack
            data-slot="page-section-panel"
            gap="none"
            bg="panel"
            border="line"
            radius="panel"
            className="overflow-hidden"
          >
            {titleBlock}
            <Stack gap="md" className={CONTAINED_BODY_CLASS[bodyInset]}>
              {children}
            </Stack>
          </Stack>
        ) : (
          <Stack
            data-slot="page-section-panel"
            gap="md"
            bg="panel"
            border="line"
            radius="panel"
            className={cn("overflow-hidden", CONTAINED_BODY_CLASS[bodyInset])}
          >
            {children}
          </Stack>
        )
      ) : (
        children
      )}
    </Stack>
  );
}

interface PageFrameProps {
  title: string;
  /** One line. A second line means it wanted to be a section. */
  description?: string;
  /**
   * When true, skip the visible page header. Use when a child (e.g. Settings
   * section hero) already owns the `h1` so the title is not drawn twice.
   */
  hideTitle?: boolean;
  /** Header trailing controls; at most one primary on the surface. */
  actions?: ReactNode;
  /** The page's sections, in the order they should appear. */
  children: ReactNode;
  /** Destructive controls. Rendered last, after children, always. */
  dangerZone?: ReactNode;
  dangerTitle?: string;
  dangerDescription?: string;
}

/**
 * The page template: a readable column, a header, sections in source order, and
 * the destructive section structurally pinned to the bottom.
 */
function PageFrame({
  title,
  description,
  hideTitle = false,
  actions,
  children,
  dangerZone,
  dangerTitle = "Danger zone",
  dangerDescription,
}: PageFrameProps) {
  const dangerHeadingId = useId();
  const showHeader = !hideTitle;

  return (
    <Stack gap="xl" className={`${PAGE_COLUMN_CLASS} ${PAGE_STACK_CLASS}`}>
      {showHeader ? (
        <Stack render={<header />} gap="xs">
          <Inline gap="lg" justify="between" align="start" wrap>
            <Box
              render={<h1 />}
              className="text-page font-semibold tracking-tightish text-ink"
            >
              {title}
            </Box>
            {actions === undefined ? null : (
              <Inline gap="sm" align="center">
                {actions}
              </Inline>
            )}
          </Inline>
          {description === undefined ? null : (
            <Box render={<p />} className="text-body text-ink-subtle">
              {description}
            </Box>
          )}
        </Stack>
      ) : null}

      {children}

      {dangerZone === undefined ? null : (
        <Stack
          render={<section aria-labelledby={dangerHeadingId} />}
          gap="md"
          className={DANGER_SEAM_CLASS}
        >
          <Stack gap="xs">
            <Inline gap="sm" align="center">
              <Icon icon={AlertTriangle} size="sm" className="text-risk" />
              <Box
                render={<h2 id={dangerHeadingId} />}
                className="text-title font-medium text-risk"
              >
                {dangerTitle}
              </Box>
            </Inline>
            {dangerDescription === undefined ? null : (
              <Box render={<p />} className="text-label text-ink-subtle">
                {dangerDescription}
              </Box>
            )}
          </Stack>
          {dangerZone}
        </Stack>
      )}
    </Stack>
  );
}

export {
  PageFrame,
  PageSection,
  PAGE_FRAME_RULES,
  PAGE_SECTION_RULES,
  PAGE_STACK_CLASS,
};
export type {
  PageFrameProps,
  PageSectionHeaderPlacement,
  PageSectionInset,
  PageSectionProps,
};
