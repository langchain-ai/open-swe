"use client";

/*
 * Rules for ConversationRow.
 *
 * Inbox agent-held conversation — carved out of QueueRow. Geometry: 32px mark
 * lane, two-line identity (contact over account), trailing meta-over-badge
 * column. Unread = weight + a corner badge on the ProviderMark (never a left
 * gutter, never colour alone). The row is click-to-open. There is no overflow
 * menu and no hover-revealed action.
 */

import type { KeyboardEvent, ReactNode } from "react";

import { Badge } from "../ui/badge";
import { Box, Inline, Stack } from "../ui/box";
import { cn } from "../ui/cn";
import {
  Building2,
  CheckCircle,
  Clock,
  Inbox,
  MessageSquare,
  type Glyph,
} from "../ui/glyphs";
import { Icon } from "../ui/icon";

import { ProviderMark, type ProviderId } from "./provider-mark";

const CONVERSATION_ROW_RULES: readonly string[] = [
  "An inbox row is not a QueueRow. It is a conversation rung with a 32px mark lane, contact over account, and a trailing column of meta over the status chip — never meta inside the identity and badge floating mid-row.",
  "Two type rungs only: `text-label` for contact, `text-meta` for the second line and time. Flat list: second line is the account, with Building2 so the company is a mark and not a caption. Under an account group: second line is the mailbox when the title is a person, otherwise activity (`signalLine`). The building stays on the section. No preview third line. No `leading-*` overrides — the type pair owns the line height.",
  "Unread is weight plus a primary corner badge on the ProviderMark (`size-1.5`, top-right of the mark lane, canvas ring so it reads on the well) — never a left gutter, never a second trailing clock-dot, and never colour alone.",
  "The row is click-to-open. There is no overflow menu, no hover-revealed action, and no reserved action lane. Time and status stay put. Edit lives on the work-pane ApprovalArtifact.",
  "Statuses are the locked four: NEEDS_YOU, SCHEDULED, WAITING, CLOSED. Machine tokens stay in data; chip copy is always the sentence-case label from `CONVERSATION_STATUS_LABEL` (Needs you / Scheduled / Waiting / Closed) — never underscored enum text. Brand pipeline states belong on send-pipeline objects, not this row.",
  "Status chips are filled `Badge tier=\"quiet\"` via `ConversationStatusBadge` — tinted wash + tone ink + the status glyph. Never `notable` outline pills on this row; outline is for one-off emphasis elsewhere, not the inbox status lane.",
];

type ConversationStatus = "NEEDS_YOU" | "SCHEDULED" | "WAITING" | "CLOSED" | "WORKING";

type ConversationStatusTone =
  | "attention"
  | "info"
  | "neutral"
  | "positive"
  | "risk";

/** User-facing chip / tab copy for the locked inbox statuses. */
const CONVERSATION_STATUS_LABEL: Record<ConversationStatus, string> = {
  CLOSED: "Archived",
  NEEDS_YOU: "Needs attention",
  WORKING: "Working",
  SCHEDULED: "Scheduled",
  WAITING: "Waiting",
};

const CONVERSATION_STATUS_ICON: Record<ConversationStatus, Glyph> = {
  WORKING: MessageSquare,
  CLOSED: CheckCircle,
  NEEDS_YOU: Inbox,
  SCHEDULED: Clock,
  WAITING: MessageSquare,
};

/** Filled status pill for the conversation row trailing lane. */
function ConversationStatusBadge({
  status,
  label,
  tone = "neutral",
}: {
  status: ConversationStatus;
  label?: string;
  tone?: ConversationStatusTone;
}) {
  return (
    <Badge tier="quiet" tone={tone}>
      <Icon icon={CONVERSATION_STATUS_ICON[status]} />
      {label ?? CONVERSATION_STATUS_LABEL[status]}
    </Badge>
  );
}

interface ConversationRowProps {
  id: string;
  contact: string;
  /**
   * Second rung. Account when the list is flat; mailbox or activity when
   * an account group already named the company.
   */
  account?: string;
  /**
   * What the second rung is. `account` gets the building mark; `activity`
   * does not — the section header already named the company.
   */
  accountKind?: "account" | "activity";
  meta?: string;
  statusBadge?: ReactNode;
  provider?: ProviderId;
  unread?: boolean;
  selected?: boolean;
  onOpen?: () => void;
}

function ConversationRow({
  account,
  accountKind = "account",
  contact,
  id,
  meta,
  onOpen,
  provider,
  selected = false,
  statusBadge,
  unread = false,
}: ConversationRowProps) {
  const activate = () => onOpen?.();
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activate();
    }
  };

  return (
    <Inline
      data-slot="conversation-row"
      data-testid={`conversation-row-${id}`}
      role="option"
      aria-selected={selected}
      tabIndex={0}
      onClick={activate}
      onKeyDown={onKeyDown}
      gap="sm"
      align="center"
      className={cn(
        "min-h-row-convo w-full cursor-pointer border-b border-line px-3 py-2 outline-none last:border-b-0 hover:bg-hover focus-visible:ring-2 focus-visible:ring-primary",
        selected && "bg-selected"
      )}
    >
      {/*
       * 32px mark lane — ProviderMark centres inside it; unread is a corner
       * badge on the well, not a reserved gutter and not a trailing clock-dot.
       */}
      <Box className="relative flex size-8 shrink-0 items-center justify-center">
        {provider ? (
          <ProviderMark provider={provider} />
        ) : (
          <Box className="inline-flex size-6 items-center justify-center rounded-compact border border-line bg-muted text-label font-medium text-ink">
            {contact.trim().charAt(0).toUpperCase() || "?"}
          </Box>
        )}
        {unread ? (
          <Box
            data-slot="conversation-unread"
            aria-hidden
            className="absolute top-0.5 right-0.5 size-1.5 rounded-full bg-primary ring-2 ring-canvas"
          />
        ) : null}
      </Box>

      <Stack gap="none" className="min-w-0 flex-1">
        <Box
          render={<span />}
          className={cn(
            "truncate text-label text-ink",
            unread ? "font-semibold" : "font-medium"
          )}
        >
          {contact}
        </Box>
        {account ? (
          <Inline align="center" gap="xs" className="min-w-0">
            {accountKind === "account" ? (
              <Icon icon={Building2} size="sm" className="text-ink-muted" />
            ) : null}
            <Box
              render={<span />}
              className="min-w-0 truncate text-meta text-ink-muted"
            >
              {account}
            </Box>
          </Inline>
        ) : null}
      </Stack>

      <Stack
        gap="xs"
        align="end"
        data-slot="conversation-row-meta"
        className="w-28 shrink-0"
      >
        {meta ? (
          <Box
            render={<span />}
            className="text-meta text-ink-subtle tabular-nums"
          >
            {meta}
          </Box>
        ) : null}
        {statusBadge ? (
          <Box className="flex min-h-badge items-center justify-end">
            {statusBadge}
          </Box>
        ) : null}
      </Stack>
    </Inline>
  );
}

export {
  ConversationRow,
  ConversationStatusBadge,
  CONVERSATION_ROW_RULES,
  CONVERSATION_STATUS_LABEL,
};
export type {
  ConversationRowProps,
  ConversationStatus,
  ConversationStatusTone,
};
