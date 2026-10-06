"use client";

/*
 * Rules for DecisionRow.
 *
 * Home ranked queue item — 48px conversation rung with source mark + rank
 * reason. Decision 1.2: the row's only verb is Open. No action lane.
 * CopyGrammar: Object → State → Evidence → Action → Receipt.
 *
 * `DecisionList` is the row's owning semantic list. A route-only row is a
 * link; the fallback button exists for non-routing examples.
 */

import { Children, type ReactNode } from "react";
import { HostLink as Link } from "../host";

import { Box, Inline, Stack } from "../ui/box";
import { cn } from "../ui/cn";

import { ProviderMark, type ProviderId } from "./provider-mark";

const DECISION_ROW_RULES: readonly string[] = [
  "Home is a router. The row's only verb is Open — never Approve, Send, or Snooze on the Home queue (platform decision 1.2). Owning surfaces hold the decision UX.",
  "Mount rows inside `DecisionList`, never straight into a Stack. The owner is a named `ul`, every row is an `li`, and route-only rows are real links so open-in-new-tab, copy-link, and browser status work without JavaScript.",
  "Geometry is the conversation rung: 48px minimum, source mark lane, identity stack, trailing column of state over time. The badge never shares the title baseline. There is no action lane and no 165px primary cluster from the Home board — that board violates one-primary and is dead.",
  "Every item carries the agent's one-line ranking reason as the secondary line, visible without expanding (decision 1.4). Fuller reasoning is one hop away in the owning surface.",
  "CopyGrammar field order: Object · State · Evidence in the identity; never 'I found something interesting!' Rank is position and weight, never a pill.",
  "Unread is weight plus an optional dot, never colour alone. Nothing animates.",
];

interface DecisionListProps {
  /** Names the queue. "Needs your decision". */
  label: string;
  /** `DecisionRow` elements, in the order the server ranked them. */
  children: ReactNode;
}

/**
 * The named list a `DecisionRow` lives in so ranked order is announced as a
 * set rather than as unrelated widgets.
 */
function DecisionList({ children, label }: DecisionListProps) {
  return (
    <Stack
      render={<ul />}
      data-slot="decision-list"
      aria-label={label}
      gap="none"
      className="w-full"
    >
      {Children.map(children, (child) => (
        <Box render={<li />} className="w-full border-b border-line last:border-b-0">
          {child}
        </Box>
      ))}
    </Stack>
  );
}

interface DecisionRowProps {
  id: string;
  /** Object name — account, campaign, contact. */
  object: string;
  /** Agent ranking reason (required). */
  reason: string;
  meta?: string;
  badge?: ReactNode;
  provider?: ProviderId;
  unread?: boolean;
  selected?: boolean;
  href?: string;
  onOpen?: () => void;
}

function DecisionRow({
  badge,
  href,
  id,
  meta,
  object,
  onOpen,
  provider,
  reason,
  selected = false,
  unread = false,
}: DecisionRowProps) {
  return (
    <Inline
      render={
        href === undefined ? (
          <button type="button" onClick={onOpen} />
        ) : (
          <Link href={href} onClick={onOpen} />
        )
      }
      data-slot="decision-row"
      data-testid={`decision-row-${id}`}
      aria-current={selected ? "page" : undefined}
      gap="sm"
      align="center"
      className={cn(
        "min-h-row-convo w-full cursor-pointer px-3 py-2 text-left outline-none hover:bg-hover focus-visible:ring-2 focus-visible:ring-primary",
        selected && "bg-selected"
      )}
    >
      <Box className="flex w-6 shrink-0 justify-center">
        {provider ? (
          <ProviderMark provider={provider} />
        ) : (
          <Box
            aria-hidden
            className={cn(
              "size-2.5 rounded-full",
              unread ? "bg-primary" : "bg-transparent"
            )}
          />
        )}
      </Box>
      <Stack gap="none" className="min-w-0 flex-1">
        <Box
          render={<span />}
          className={cn(
            "truncate text-label text-ink",
            unread ? "font-semibold" : "font-medium"
          )}
        >
          {object}
        </Box>
        <Box
          render={<span />}
          className="truncate text-meta text-ink-subtle"
        >
          {reason}
        </Box>
      </Stack>
      {badge === undefined && meta === undefined ? null : (
        <Stack
          data-slot="decision-row-meta"
          gap="xs"
          align="end"
          className="w-28 shrink-0"
        >
          {badge === undefined ? null : (
            <Box className="flex min-h-badge items-center justify-end">
              {badge}
            </Box>
          )}
          {meta === undefined ? null : (
            <Box
              render={<span />}
              className="truncate text-right text-meta tabular-nums text-ink-subtle"
            >
              {meta}
            </Box>
          )}
        </Stack>
      )}
    </Inline>
  );
}

export { DecisionList, DecisionRow, DECISION_ROW_RULES };
export type { DecisionListProps, DecisionRowProps };
