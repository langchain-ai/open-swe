"use client";

/*
 * Rules for AgentThread.
 *
 * CORE 05/12 conversation column: 704px measure (documented exception),
 * user right-aligned, agent content full-width, interpretation block
 * with speech-tail radius, centred day dividers.
 *
 * Bubbles are ONE surface with ONE pad. Never Frame here — Frame is mat +
 * panel (gutter + panel pad), which is the double-padding the boards forbid
 * on a speech bubble.
 */

import { useState, type ReactNode } from "react";

import { Box, Stack } from "../ui/box";
import { Button } from "../ui/button";
import { cn } from "../ui/cn";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { Check, Copy } from "../ui/glyphs";
import { Icon } from "../ui/icon";
import {
  SCROLL_HOST_CLASS,
  ScrollAreaBody,
} from "../ui/scroll-area";

const AGENT_THREAD_RULES: readonly string[] = [
  "The thread measure is 704px — a documented contentWidth exception, not a fifth rung (`APP_SHELL_RULES`). Centre it in the work pane. The number lives in one place, `--gtm-container-thread`, reached as `max-w-(--container-thread)`: an exception is named once, never retyped as an arbitrary value.",
  "User turns are right-aligned bubbles. Agent turns use the full content width, without an avatar or mirrored bubble.",
  "The interpretation block is a single muted surface with one pad (`px-3 py-2.5`) and an asymmetric radius (speech-tail on the bottom-leading corner). It is the only asymmetric radius in the product.",
  "Do not compose bubbles from Frame. Frame is mat + panel — two pads — and that is why interpretation looked double-inset. Frame belongs to artifacts (ApprovalArtifact), not speech.",
  "Day dividers are centred meta text with no rule. Tool groups and ApprovalArtifacts compose inside agent turns; they are not this pattern's job.",
  "Long user copy stays speech. Estimate more than sixteen visual lines (a newline or a wrap at the bubble measure) and clamp with `line-clamp-16`. View more opens a dialog for the rest, with Copy. Mentions and real file chips stay outside the clamp. Do not expand in the bubble, and do not mint a fake attachment.",
  "User turns keep hover/focus Copy. The transcript shows assistant Copy only on its last prose block, after its tools, resources, receipts, and structured view, once streaming ends. `TurnCopyButton` copies the source string and swaps to Check. Intermediate assistant blocks have no Copy. Code fences keep their own copy.",
];

/** One pad for every bubble — Frame's mat+panel stack is forbidden here. */
const BUBBLE_PAD_CLASS = "px-3 py-2.5";

interface AgentThreadProps {
  children: ReactNode;
  className?: string;
}

function AgentThread({ children, className }: AgentThreadProps) {
  return (
    <Stack
      data-slot="agent-thread"
      data-testid="agent-thread"
      gap="md"
      className={cn("mx-auto w-full max-w-(--container-thread) py-4", className)}
    >
      {children}
    </Stack>
  );
}

interface AgentThreadDividerProps {
  label: string;
}

function AgentThreadDivider({ label }: AgentThreadDividerProps) {
  return (
    <Box
      data-slot="agent-thread-divider"
      render={<p />}
      className="self-center text-meta tracking-caps text-ink-subtle uppercase"
    >
      {label}
    </Box>
  );
}

interface AgentInterpretationProps {
  children: ReactNode;
  className?: string;
}

function AgentInterpretation({
  children,
  className,
}: AgentInterpretationProps) {
  return (
    <Box
      data-slot="agent-interpretation"
      className={cn(
        "rounded-control rounded-bl-none bg-muted text-label text-ink",
        BUBBLE_PAD_CLASS,
        className
      )}
    >
      {children}
    </Box>
  );
}

interface AgentTurnProps {
  /** Accessible label for the agent's turn. */
  name?: string;
  children: ReactNode;
}

function AgentTurn({ children, name = "Agent" }: AgentTurnProps) {
  return (
    <Stack data-slot="agent-turn" role="group" aria-label={name} gap="sm" className="min-w-0 w-full">
      {children}
    </Stack>
  );
}

/** Body lines that stay visible before View more. `text-body` is 14/20. */
const USER_TURN_COLLAPSED_LINES = 16;
/** Approx. characters that fit one `max-w-lg` body line. */
const USER_TURN_WRAP_CHARS = 70;
const USER_TURN_COPY_CLASS = "m-0 break-words whitespace-pre-wrap";
const USER_TURN_COPY_CLAMP_CLASS = "line-clamp-16";
const USER_TURN_MORE_CLASS =
  "cursor-pointer self-start rounded-compact text-meta font-medium text-ink-subtle outline-none hover:text-ink focus-visible:ring-2 focus-visible:ring-primary";
const USER_TURN_DIALOG_CLASS =
  "flex max-h-dvh min-h-0 flex-col gap-0 overflow-hidden p-0 sm:max-w-reading!";
const USER_TURN_DIALOG_HEADER_CLASS = "shrink-0 px-4 pt-4";
const USER_TURN_DIALOG_BODY_CLASS = `${SCROLL_HOST_CLASS} min-h-0 flex-1`;
const USER_TURN_DIALOG_PROSE_CLASS =
  "px-4 py-3 text-body break-words whitespace-pre-wrap text-ink";
const USER_TURN_DIALOG_FOOTER_CLASS = "mx-0 mb-0 shrink-0";
const COPIED_ACKNOWLEDGEMENT_MS = 1_600;
const TURN_COPY_CLASS =
  "opacity-0 transition-opacity duration-fast ease-out-quint group-hover/turn:opacity-100 group-focus-within/turn:opacity-100 motion-reduce:transition-none";

function copyPlainText(text: string, onCopied: () => void): void {
  const clipboard = navigator.clipboard;
  if (clipboard === undefined || typeof clipboard.writeText !== "function") {
    return;
  }
  void Promise.resolve(clipboard.writeText(text)).then(onCopied, () => undefined);
}

function TurnCopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  function handleCopy() {
    copyPlainText(text, () => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), COPIED_ACKNOWLEDGEMENT_MS);
    });
  }

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      data-slot="turn-copy"
      aria-label={copied ? "Copied" : "Copy message"}
      className={TURN_COPY_CLASS}
      onClick={handleCopy}
    >
      <Icon icon={copied ? Check : Copy} size="sm" />
    </Button>
  );
}

function userTurnCopyOverflows(text: string): boolean {
  let visual = 0;
  let start = 0;
  while (start <= text.length) {
    const newline = text.indexOf("\n", start);
    const end = newline === -1 ? text.length : newline;
    visual += Math.max(1, Math.ceil((end - start) / USER_TURN_WRAP_CHARS));
    if (visual > USER_TURN_COLLAPSED_LINES) return true;
    if (newline === -1) break;
    start = newline + 1;
  }
  return false;
}

interface UserTurnCopyProps {
  children?: ReactNode;
  text: string;
  /** Visible prose and compact pill labels, when different from the full copy text. */
  displayText?: string;
}

interface UserTurnMessageDialogProps {
  children: ReactNode;
  onOpenChange: (open: boolean) => void;
  open: boolean;
  text: string;
}

function UserTurnMessageDialog({
  children,
  onOpenChange,
  open,
  text,
}: UserTurnMessageDialogProps) {
  const [copied, setCopied] = useState(false);

  function handleOpenChange(next: boolean) {
    onOpenChange(next);
    if (!next) setCopied(false);
  }

  function handleCopy() {
    copyPlainText(text, () => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), COPIED_ACKNOWLEDGEMENT_MS);
    });
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        data-testid="user-turn-dialog"
        className={USER_TURN_DIALOG_CLASS}
      >
        <DialogHeader className={USER_TURN_DIALOG_HEADER_CLASS}>
          <DialogTitle>Your message</DialogTitle>
          <DialogDescription className="sr-only">
            Full text of this message
          </DialogDescription>
        </DialogHeader>
        <Box className={USER_TURN_DIALOG_BODY_CLASS}>
          <ScrollAreaBody overflow="vertical">
            <Box
              render={<p />}
              data-slot="user-turn-dialog-copy"
              className={USER_TURN_DIALOG_PROSE_CLASS}
            >
              {children}
            </Box>
          </ScrollAreaBody>
        </Box>
        <DialogFooter className={USER_TURN_DIALOG_FOOTER_CLASS}>
          <Button
            type="button"
            variant="outline"
            size="compact"
            onClick={handleCopy}
          >
            <Icon icon={copied ? Check : Copy} size="sm" />
            {copied ? "Copied" : "Copy"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * User-turn prose that can overflow. Mentions and file chips stay with the
 * caller so a clamp never hides the turn's context.
 */
function UserTurnCopy({ children, text, displayText }: UserTurnCopyProps) {
  const overflows = userTurnCopyOverflows(displayText ?? text);
  const [open, setOpen] = useState(false);
  const body = children ?? text;

  const copy = (
    <Box
      render={<p />}
      data-slot="user-turn-copy"
      className={cn(
        USER_TURN_COPY_CLASS,
        overflows ? USER_TURN_COPY_CLAMP_CLASS : undefined
      )}
    >
      {body}
    </Box>
  );

  if (!overflows) return copy;

  return (
    <Stack gap="sm">
      {copy}
      <Box
        render={<button type="button" />}
        data-slot="user-turn-more"
        data-testid="user-turn-more"
        aria-expanded={open}
        aria-haspopup="dialog"
        className={USER_TURN_MORE_CLASS}
        onClick={() => setOpen(true)}
      >
        View more
      </Box>
      <UserTurnMessageDialog
        open={open}
        text={text}
        onOpenChange={setOpen}
      >
        {body}
      </UserTurnMessageDialog>
    </Stack>
  );
}

interface UserTurnProps {
  children: ReactNode;
  /** Source string for the hover copy. String children supply it. */
  copyText?: string;
}

function UserTurn({ children, copyText }: UserTurnProps) {
  const text = copyText ?? (typeof children === "string" ? children : null);

  return (
    <Box
      data-slot="user-turn"
      className="group/turn flex w-full justify-end"
    >
      <Stack gap="none" className="min-w-0 max-w-lg items-end">
        <Box
          className={cn(
            "max-w-full rounded-panel rounded-br-none border border-line bg-sidebar text-body text-ink",
            BUBBLE_PAD_CLASS
          )}
        >
          {typeof children === "string" ? (
            <UserTurnCopy text={children} />
          ) : (
            children
          )}
        </Box>
        {text && text.trim() !== "" ? <TurnCopyButton text={text} /> : null}
      </Stack>
    </Box>
  );
}

export {
  AgentInterpretation,
  AgentThread,
  AgentThreadDivider,
  AgentTurn,
  TurnCopyButton,
  UserTurn,
  UserTurnCopy,
  AGENT_THREAD_RULES,
};
export type {
  AgentInterpretationProps,
  AgentThreadDividerProps,
  AgentThreadProps,
  AgentTurnProps,
  UserTurnCopyProps,
  UserTurnProps,
};
