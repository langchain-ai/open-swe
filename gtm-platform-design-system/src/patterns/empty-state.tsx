"use client";

/*
 * Rules for EmptyState.
 *
 * Zero-data surfaces get a quiet visual centre, not a lonely caption and not
 * ambient motion. Composition follows the shadcn / ReUI Empty family
 * (`ui/empty` — see `web/reference/reui/empty/` for pulled examples). Empty is
 * settled: never Orb, never Spinner, never continuous decoration.
 */

import type { ReactNode } from "react";

import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "../ui/empty";
import type { Glyph } from "../ui/glyphs";
import { Icon } from "../ui/icon";

const EMPTY_STATE_RULES: readonly string[] = [
  "Compose `ui/empty` (Header → Media → Title → Description, optional Content). Never a bare centred `<p>` pretending to be an empty surface. Reference: ReUI `c-empty-*` under `web/reference/reui/empty/`.",
  "Media is still: a muted glyph well (`icon`) or a sanctioned brand mark tile (`media`). Never mount `ui/orb` / thinking-orbs, Spinner, or continuous orbit/breathe decoration: an empty surface is settled, and filter empties are seen too often for ambient motion.",
  "Title is `text-title` semibold; description is `text-body` ink-subtle. One primary action in Content when recovery is possible; omit Content when the surface already owns the next step (e.g. Chat composer below).",
  "Copy stays short and plain: one short title, one short sentence max. No jargon (ledger, upstream, status-filtered, bridge), no over-explaining how the system works. Prefer silence over a second sentence.",
  "Example prompts and recovery cards are controls, not decoration. Activating one must start the exact flow its copy promises, preserve any composed text, and provide the same keyboard and touch path as a primary action. If the route cannot perform the action, omit the card and say where the action lives.",
  "No enter/exit choreography on the empty itself. If the surface swaps list ↔ empty, the swap is instant — empty is a state, not a celebration.",
];

interface EmptyStateProps {
  title: string;
  description?: string;
  icon?: Glyph;
  /**
   * Custom still media (e.g. LangChain mark tile). Wins over `icon` when both
   * are set. Caller owns the well; keep it motionless.
   */
  media?: ReactNode;
  /** Recovery controls — omit when the surface already owns the next step. */
  action?: ReactNode;
  className?: string;
}

/** Product empty surface: media + title + description + optional action. */
function EmptyState({
  action,
  className,
  description,
  icon,
  media,
  title,
}: EmptyStateProps) {
  const mediaNode =
    media !== undefined ? (
      media
    ) : icon ? (
      <EmptyMedia variant="icon" aria-hidden>
        <Icon icon={icon} size="lg" />
      </EmptyMedia>
    ) : null;

  return (
    <Empty
      data-testid="empty-state"
      className={className}
      role="status"
    >
      <EmptyHeader>
        {mediaNode}
        <EmptyTitle>{title}</EmptyTitle>
        {description ? (
          <EmptyDescription>{description}</EmptyDescription>
        ) : null}
      </EmptyHeader>
      {action ? <EmptyContent>{action}</EmptyContent> : null}
    </Empty>
  );
}

export { EmptyState, EMPTY_STATE_RULES };
export type { EmptyStateProps };
