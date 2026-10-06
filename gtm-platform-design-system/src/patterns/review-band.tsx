"use client";

/*
 * Rules for ReviewBand.
 *
 * The rules themselves are `REVIEW_BAND_RULES` below, not this comment.
 * `/design` renders that array verbatim beside the live pattern.
 *
 * A review band is the one muted panel that presents a single object to act
 * on. Suggested play on Home is the first consumer. Hand-building the same
 * Inline at a call site is a violation: the pace (title, copy, meta, actions)
 * is the decision this file owns.
 */

import type { ReactElement, ReactNode } from "react";

import { Box, Inline, Stack } from "../ui/box";
import { cn } from "../ui/cn";

const REVIEW_BAND_RULES: readonly string[] = [
  "A review band presents one object to act on. It is not a list row, not a New-this-week card, and not a contained PageSection. Suggested play is a titled flat section; this band is the object inside it (`PAGE_SECTION_RULES`).",
  "Anatomy is fixed: optional leading mark, then title / description / meta in one stack, then optional trailing actions. Actions never sit in the title row. A 32px control in that stack is what made the title-to-copy gap larger than the copy-to-meta gap.",
  "The text stack uses one gap (`md`). Title-to-description and description-to-meta are the same interval. Do not insert a second stack or a justified header inside the body.",
  "Description is `text-body` (14/20). It is read, not scanned. Do not set it on `text-label` and do not add a `leading-*` override. Title defaults to `text-title` semibold; a conversation adapter lowers it through the shared `text-label` object-title class. Meta is `text-meta`.",
  "The panel is muted fill, one hairline, panel radius, `padding=\"lg\"`, and `gap=\"xl\"` between mark, body, and actions. That is the whole treatment. No shadow, no second fill, no tighter call-site override.",
  "A surface that needs this object renders ReviewBand. Reaching for Inline + IconWell + a custom stack to recreate it is a gap in this pattern only if the slots cannot express the object; grow the slots here, once.",
];

interface ReviewBandProps {
  /** Trailing verbs. Compact controls. Never composed into the title row. */
  actions?: ReactNode;
  className?: string;
  /** Prose. Always painted as `text-body`. */
  description: ReactNode;
  descriptionId?: string;
  /** Leading IconWell or brand mark. Omit when the section title already named the kind. */
  mark?: ReactNode;
  /** Facts under the copy: time, campaign, targets. Always `text-meta`. */
  meta?: ReactNode;
  /** Swap the root for a button or link when the whole band is the control. */
  render?: ReactElement;
  title: ReactNode;
  /** Semantic type-rung override for conversation-scale objects. */
  titleClassName?: string;
  titleId?: string;
}

function ReviewBand({
  actions,
  className,
  description,
  descriptionId,
  mark,
  meta,
  render,
  title,
  titleClassName,
  titleId,
}: ReviewBandProps) {
  return (
    <Inline
      data-slot="review-band"
      render={render}
      gap="xl"
      align="start"
      bg="muted"
      border="line"
      radius="panel"
      padding="lg"
      className={cn("w-full", className)}
    >
      {mark}
      <Stack data-slot="review-band-body" gap="md" className="min-w-0 flex-1">
        <Box
          data-slot="review-band-title"
          id={titleId}
          render={<span />}
          className={cn(
            "text-pretty text-title font-semibold text-ink",
            titleClassName
          )}
        >
          {title}
        </Box>
        <Box
          data-slot="review-band-description"
          id={descriptionId}
          render={<p />}
          className="text-pretty text-body text-ink-subtle"
        >
          {description}
        </Box>
        {meta === undefined ? null : (
          <Box
            data-slot="review-band-meta"
            className="text-meta text-ink-subtle"
          >
            {meta}
          </Box>
        )}
      </Stack>
      {actions === undefined ? null : (
        <Box data-slot="review-band-actions" className="shrink-0">
          {actions}
        </Box>
      )}
    </Inline>
  );
}

export { ReviewBand, REVIEW_BAND_RULES };
export type { ReviewBandProps };
