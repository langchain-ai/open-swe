"use client";

/*
 * Rules for VersionedEditor + PublishGate.
 *
 * Third save-semantics family (CORE 04 Campaign Studio): prompt edits stay
 * browser-local until Save draft. Publish creates the immutable version.
 * Uses the conversation draft anatomy: a quiet header on the Frame mat,
 * one inset content panel, and one persistent action footer.
 */

import type { KeyboardEvent, MouseEvent, ReactNode } from "react";

import { ConfirmableAction } from "./confirmable-action";
import { Box, Inline } from "../ui/box";
import { Button } from "../ui/button";
import { cn } from "../ui/cn";
import {
  Frame,
  FrameDescription,
  FrameFooter,
  FrameHeader,
  FramePanel,
  FrameTitle,
} from "../ui/frame";

const VERSIONED_EDITOR_RULES: readonly string[] = [
  "VersionedEditor is the third save family. Prompt changes stay browser-local until Save draft updates the one mutable team draft or Publish creates the immutable version. Local, Saving draft, Draft saved, Publishing, and Publish failed belong beside the actions, not in a toast. There is no page-level dirty bar.",
  "Publish is consequential but NOT destructive. It names its consequence in the summary and again in the button ('Publish v3'). It does not need a confirmation dialog.",
  "Discard draft IS destructive, so it goes through `ConfirmableAction`. Publish and Discard share a footer and must never swap grammars: confirm the one that destroys, not the one that ships.",
  "Rollback is republish, never delete. Returning to v2 publishes v2 again as the newest live pointer; no version is ever removed and this family has no Delete verb. History that can be erased cannot be evidence.",
  "Show version label + draft badge with existing Badge tiers — never invent a fourth draft colour.",
  "A version diff is `ChangeSet` / `DiffRow` in the `diff` slot — the same evidence object a Receipt uses for proposed writes (`DIFF_ROW_RULES`). Never a bespoke side-by-side editor, never a second diff vocabulary.",
  "`dryRun` is a reserved slot in the gate footer, held for the dry-run fast-follow. Empty until it lands; when it lands it is a Quiet/outline action left of Discard — never a second primary, and never a step Publish is blocked on.",
  "Follow the conversation draft anatomy: a quiet header on the Frame mat, one inset content panel, and one persistent trailing action footer. Do not duplicate actions in the header and footer.",
  "Compose under RecordHeader. Provisional StatReadout may sit beside the gate once — not a KPI strip.",
  "A collapsed preview is this same card with a shorter body, never a second chrome. The mask fades the cut so the panel fill stays honest.",
  "The chrome row is the expand control. There is no extra chevron. Interactive children keep their own clicks. Collapse interpolates a grid track from minmax(6rem, 0fr) to minmax(6rem, 1fr) at duration-fast ease-out-quint. Keep the inner content size stable so both directions stay smooth. Reduced motion is a cut.",
  "When the surface owns versions, the header leading slot is the version picker. Do not also print the same label as FrameTitle. The footer shows Edit and one Publish action while reviewing, replaced by Discard changes and Save draft while editing. Discard changes only restores the saved content; it never deletes a server draft. Share and Retire change the whole record and sit in RecordHeader actions. Compare, Copy, and Download live under a three-dots menu.",
];

const EDITOR_BODY_CLASS = "text-body text-ink";
const EDITOR_BODY_FULL_CLASS = "min-h-48";
const EDITOR_REVEAL_CLASS =
  "grid min-h-0 transition-[grid-template-rows] duration-fast ease-out-quint motion-reduce:transition-none";
const EDITOR_REVEAL_OPEN_CLASS = "grid-rows-[minmax(6rem,1fr)]";
const EDITOR_REVEAL_COLLAPSED_CLASS = "grid-rows-[minmax(6rem,0fr)]";
const EDITOR_REVEAL_INNER_CLASS = "min-h-0 overflow-hidden";
const EDITOR_BODY_MASK_CLASS =
  "[mask-image:linear-gradient(to_bottom,black_70%,transparent)] [-webkit-mask-image:linear-gradient(to_bottom,black_70%,transparent)]";
const CHROME_TOGGLE_CLASS = "cursor-pointer";

interface VersionedEditorProps {
  versionLabel?: string;
  /** Replaces the static version title when the surface owns a picker. */
  leading?: ReactNode;
  draftBadge?: ReactNode;
  toolbar?: ReactNode;
  children: ReactNode;
  className?: string;
  /** Same card, shorter body. The full prompt uses the expanded body. */
  collapsed?: boolean;
  /** Click empty chrome to expand or collapse. Interactive children stop this. */
  onChromeToggle?: () => void;
  /** ChangeSet / DiffRow comparison against live, revealed by the toolbar. */
  diff?: ReactNode;
  /** Optional PublishGate rendered as the stacked footer panel. */
  gate?: ReactNode;
}

function stopChromeToggle(event: MouseEvent<HTMLDivElement>) {
  event.stopPropagation();
}

function chromeToggleLabel(collapsed: boolean): string {
  return collapsed ? "Show full prompt" : "Hide full prompt";
}

function VersionedEditor({
  children,
  className,
  collapsed = false,
  diff,
  draftBadge,
  gate,
  leading,
  onChromeToggle,
  toolbar,
  versionLabel,
}: VersionedEditorProps) {
  const chromeToggle = onChromeToggle !== undefined;

  function onChromeKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!chromeToggle || event.target !== event.currentTarget) return;
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onChromeToggle?.();
  }

  return (
    <Frame
      data-slot="versioned-editor"
      data-testid="versioned-editor"
      spacing="default"
      className={cn("w-full", className)}
    >
      <FrameHeader
        data-chrome="true"
        role={chromeToggle ? "button" : undefined}
        tabIndex={chromeToggle ? 0 : undefined}
        aria-expanded={chromeToggle ? !collapsed : undefined}
        aria-label={chromeToggle ? chromeToggleLabel(collapsed) : undefined}
        className={cn("px-1.5 py-1", chromeToggle ? CHROME_TOGGLE_CLASS : undefined)}
        onClick={onChromeToggle}
        onKeyDown={chromeToggle ? onChromeKeyDown : undefined}
      >
        <Inline gap="sm" align="center" justify="between">
          <Inline
            gap="sm"
            align="center"
            onClick={chromeToggle ? stopChromeToggle : undefined}
          >
            {leading ??
              (versionLabel === undefined ? null : (
                <FrameTitle className="font-mono text-label">
                  {versionLabel}
                </FrameTitle>
              ))}
            {draftBadge}
          </Inline>
          {toolbar === undefined ? null : (
            <Box onClick={chromeToggle ? stopChromeToggle : undefined}>
              {toolbar}
            </Box>
          )}
        </Inline>
      </FrameHeader>
      {diff ? (
        <FramePanel data-slot="versioned-editor-diff" fit>
          {diff}
        </FramePanel>
      ) : null}
      <FramePanel>
        <Box
          className={cn(
            EDITOR_REVEAL_CLASS,
            collapsed ? EDITOR_REVEAL_COLLAPSED_CLASS : EDITOR_REVEAL_OPEN_CLASS
          )}
        >
          <Box
            data-collapsed={collapsed ? "" : undefined}
            className={cn(
              EDITOR_REVEAL_INNER_CLASS,
              EDITOR_BODY_CLASS,
              collapsed ? EDITOR_BODY_MASK_CLASS : undefined
            )}
          >
            <Box className={EDITOR_BODY_FULL_CLASS}>{children}</Box>
          </Box>
        </Box>
      </FramePanel>
      {gate}
    </Frame>
  );
}

interface PublishGateProps {
  versionLabel: string;
  summary?: string;
  onPublish?: () => void;
  disabled?: boolean;
  loading?: boolean;
  /** Destructive: always routed through ConfirmableAction, never a bare click. */
  onDiscard?: () => void | Promise<void>;
  /** Reserved slot for the dry-run fast-follow. Quiet action, never a primary. */
  dryRun?: ReactNode;
  secondary?: ReactNode;
  className?: string;
}

function PublishGate({
  className,
  disabled = false,
  dryRun,
  loading = false,
  onDiscard,
  onPublish,
  secondary,
  summary,
  versionLabel,
}: PublishGateProps) {
  return (
    <FramePanel
      chrome
      data-slot="publish-gate"
      data-testid="publish-gate"
      className={cn("bg-selected", className)}
    >
      <Inline gap="sm" wrap align="center" justify="between">
        <FrameHeader className="min-w-0 flex-1 gap-0.5">
          <FrameTitle>Ready to publish {versionLabel}</FrameTitle>
          {summary ? (
            <FrameDescription className="whitespace-normal">
              {summary}
            </FrameDescription>
          ) : null}
        </FrameHeader>
        <FrameFooter className="flex-row flex-wrap items-center gap-2 sm:justify-end">
          {dryRun}
          {secondary}
          {onDiscard ? (
            <ConfirmableAction
              trigger={
                <Button type="button" variant="ghost">
                  Discard draft
                </Button>
              }
              title={`Discard draft ${versionLabel}`}
              description={`Discarding removes the unpublished ${versionLabel} draft and its edits. The live version is untouched, and the draft cannot be recovered.`}
              confirmLabel="Discard draft"
              onConfirm={async () => {
                await onDiscard();
              }}
            />
          ) : null}
          <Button
            type="button"
            disabled={disabled || loading}
            loading={loading}
            onClick={onPublish}
          >
            Publish {versionLabel}
          </Button>
        </FrameFooter>
      </Inline>
    </FramePanel>
  );
}

export { PublishGate, VersionedEditor, VERSIONED_EDITOR_RULES };
export type { PublishGateProps, VersionedEditorProps };
