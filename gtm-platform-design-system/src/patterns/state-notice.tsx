"use client";

/*
 * Rules for StateNotice.
 *
 * The rules themselves are `STATE_NOTICE_RULES` below, not this comment.
 */

import type { ReactNode } from "react";

import { Box, Inline, Stack } from "../ui/box";
import { cn } from "../ui/cn";
import type { Glyph } from "../ui/glyphs";
import { Icon } from "../ui/icon";

const STATE_NOTICE_RULES: readonly string[] = [
  "This is the one shape for a thing that did not happen: a send the door refused, a job that stopped, a connection that lapsed, an import that found nothing to do. Never invent a second one per surface.",
  "The title names what happened in the user's own words. Never 'That did not go through', never 'Error', never a status code: somebody reading six of these in a week has to tell them apart at a glance.",
  "Tone is who clears it, not how bad it is. RISK is a wall the user cannot clear alone. ATTENTION is a person clearing it, named in the sentence. INFO is time clearing it with nobody acting.",
  "One mark, one title, one sentence, at most one action. The sentence never repeats the title, and it says what happened to their work: nothing was sent, the campaign is untouched, the draft stays here.",
  "Plain words. Say mailbox, not identity; on hold, not PAUSED; we could not, not the request was refused. No verdict names, no slugs, no provider text.",
  "It sits above the object it is about and leaves that object whole, buttons live. Every state here clears, and the user's next move is the same button.",
  "Never dismissible. A notice waved away is one the user meets again at the same button with no memory of why.",
  "Use `Alert` instead for a message that is not about a blocked object: a tip, a confirmation, a page-level announcement.",
  "CopyGrammar: Object · State · Evidence · Action. The title is the state, the sentence is the evidence, the button is the action.",
];

type StateNoticeTone = "RISK" | "ATTENTION" | "INFO";

const MARK_CLASS: Record<StateNoticeTone, string> = {
  RISK: "border-risk/20 bg-risk-bg text-risk",
  ATTENTION: "border-attention/20 bg-attention-bg text-attention",
  INFO: "border-info/20 bg-info-bg text-info",
};

const PANEL_CLASS: Record<StateNoticeTone, string> = {
  RISK: "border-risk/20 bg-risk-bg/40",
  ATTENTION: "border-attention/20 bg-attention-bg/40",
  INFO: "border-info/20 bg-info-bg/40",
};

interface StateNoticeProps {
  /** What happened, in the user's words: "This address is on the do-not-email list". */
  title: string;
  /** One sentence of evidence and what it means for their work. It does not repeat the title. */
  description: string;
  icon: Glyph;
  /** Who clears it: RISK nobody here, ATTENTION a person, INFO time. */
  tone: StateNoticeTone;
  /** The one thing the user can do about it here, or nothing. */
  action?: ReactNode;
}

function StateNotice({ action, description, icon, title, tone }: StateNoticeProps) {
  return (
    <Inline
      role="alert"
      align="start"
      gap="md"
      data-slot="state-notice"
      data-tone={tone}
      className={cn("rounded-panel border p-3", PANEL_CLASS[tone])}
    >
      <Box
        render={<span />}
        aria-hidden
        className={cn(
          "inline-flex size-9 shrink-0 items-center justify-center rounded-compact border [&_svg]:size-4.5",
          MARK_CLASS[tone]
        )}
      >
        <Icon icon={icon} />
      </Box>
      <Stack gap="xs" className="min-w-0 flex-1 pt-0.5">
        <Box render={<p />} className="text-label font-medium text-ink">
          {title}
        </Box>
        <Box render={<p />} className="text-meta text-ink-subtle">
          {description}
        </Box>
        {action === undefined ? null : <Box className="pt-1">{action}</Box>}
      </Stack>
    </Inline>
  );
}

export { StateNotice, STATE_NOTICE_RULES };
export type { StateNoticeTone, StateNoticeProps };
