"use client";

/*
 * Rules for Receipt.
 *
 * Artifact-tier confirmation after a decision (CopyGrammar terminal).
 * Stacked Frame like ApprovalArtifact: chrome header → optional figure →
 * body (ChangeSet). An optional 24px mark names the result family without
 * adding another row. Title leads; figure is label-scale mono only here.
 * Diff evidence is a quiet ChangeSet subcard — never Frame-in-Frame.
 */

import type { ReactNode } from "react";

import { Box, Inline, Stack } from "../ui/box";
import { cn } from "../ui/cn";
import {
  Frame,
  FrameDescription,
  FrameHeader,
  FramePanel,
  FrameTitle,
} from "../ui/frame";

const RECEIPT_RULES: readonly string[] = [
  "Receipt is the terminal step of CopyGrammar: Object → State → Evidence → Action → Receipt. Render inline under the action and keep it inspectable.",
  "Shell is ReUI `Frame` stacked+dense — chrome header, optional figure panel, body panel. Same multi-panel family as ApprovalArtifact and VersionedEditor. Never a single stuffed panel with figure and diffs competing for one pad.",
  "A receipt may lead with one 24px IconWell or ProviderMark when the surrounding surface does not already name the result family. The mark stays in the chrome beside the title; never spend a second panel only to repeat the provider or object kind.",
  "Type order is title → summary → figure → changes. Standalone receipts use `text-title`; conversation adapters lower the title through their shared `text-label` object-title class. Figure and after values use `text-label`; summary, labels, and before values use `text-meta`.",
  "A dedicated figure block is legal only on Receipt. It is label-scale mono (`text-label`), same value type as StatReadout — Receipt presents the number; it does not invent a display rung.",
  "Field mutations mount as ChangeSet / ChangeSetSection / DiffRow in the body panel — one quiet subcard, optional subsections. Do not bury diffs in prose and do not wrap them in a second Frame.",
  "Keep the shell quiet: no Urgent accents, no second primary CTA. A Receipt is confirmation, not a new decision.",
];

interface ReceiptProps {
  title: string;
  /** Semantic type-rung override for conversation-scale outcomes. */
  titleClassName?: string;
  summary?: string;
  heroValue?: string;
  heroLabel?: string;
  children?: ReactNode;
  /** Optional 24px IconWell or ProviderMark naming the result family. */
  mark?: ReactNode;
  meta?: ReactNode;
  className?: string;
  /** Panel padding rung. Agent-thread receipts use sm; full artifacts default. */
  spacing?: "sm" | "default" | "lg";
}

function Receipt({
  children,
  className,
  heroLabel,
  heroValue,
  mark,
  meta,
  spacing = "default",
  summary,
  title,
  titleClassName,
}: ReceiptProps) {
  const hasFigure = heroValue !== undefined && heroValue !== "";
  const hasBody = children !== undefined && children !== null;

  return (
    <Frame
      data-slot="receipt"
      data-testid="receipt"
      stacked
      dense
      spacing={spacing}
      className={cn("w-full", className)}
    >
      <FramePanel chrome>
        <Inline gap="sm" align="start" justify="between">
          {mark}
          <FrameHeader className="min-w-0 flex-1 gap-0.5">
            <FrameTitle className={cn("font-semibold", titleClassName)}>
              {title}
            </FrameTitle>
            {summary ? <FrameDescription>{summary}</FrameDescription> : null}
          </FrameHeader>
          {meta}
        </Inline>
      </FramePanel>

      {hasFigure ? (
        <FramePanel>
          <Stack gap="none">
            <Box
              render={<span />}
              className="font-mono text-label font-semibold text-ink tabular-nums"
            >
              {heroValue}
            </Box>
            {heroLabel ? (
              <FrameDescription>{heroLabel}</FrameDescription>
            ) : null}
          </Stack>
        </FramePanel>
      ) : null}

      {hasBody ? <FramePanel>{children}</FramePanel> : null}
    </Frame>
  );
}

export { Receipt, RECEIPT_RULES };
export type { ReceiptProps };
