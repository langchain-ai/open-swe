"use client";

/*
 * Rules for ConfirmableAction.
 *
 * The rules themselves are `CONFIRMABLE_ACTION_RULES` below, not this comment.
 * `/design` renders that array verbatim beside the live pattern, so the
 * decision a builder reads in the gallery and the decision this file enforces
 * are the same strings, and there is exactly one place to change either.
 *
 * What the code enforces: the dialog, the explicit confirmation, and the in
 * flight lock. What the rules state: when reaching for this pattern stops being
 * optional and why a lone risk coloured button is never the whole flow.
 */

import { useRef, useState } from "react";
import type { FormEvent, ReactElement, ReactNode } from "react";

import { Box, Inline, Stack } from "../ui/box";
import { Button } from "../ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "../ui/dialog";
import { AlertTriangle, Loader2, Trash2 } from "../ui/glyphs";
import type { Glyph } from "../ui/glyphs";
import { Icon } from "../ui/icon";
import { problemMessage } from "../lib/problem";

const CONFIRMABLE_ACTION_RULES: readonly string[] = [
  "Required, not optional, for any action that permanently destroys something or sends on a rep's behalf: deleting a campaign, wiping account memory, releasing a queued send, revoking a rep's Gmail grant. If undo is impossible, the flow is this pattern.",
  "A risk coloured Button on its own is never a destructive flow. The button is only the trigger; the decision lives in the dialog. A one click destroy is a bug, however small the object.",
  "The confirmation is one standard dialog: state the consequence in plain language, keep Cancel on screen, and name the final button for its effect rather than agreement.",
  "Never ask the user to type an email, name, title, or identifier to confirm. Repeating a label is ceremony, not authorization, and it trains people to move through destructive flows mechanically.",
  "Do not invent a second register at a call site. A hand-rolled arming boolean with its own Keep/Delete pair is this pattern, badly, and duplicated.",
  "The pattern owns its dialog one of two ways and never both. Pass `trigger` and it opens on press, which is the common case. Mount it with `onDismiss` when the host already knows which object is being acted on and there is no button left to press: a row menu that names a thread or a band that names the record on screen. Unmount it when dismissed. Either way the dialog and failure line are this pattern's.",
  "Cancel is always on screen, and Escape or a press outside close the dialog for free, right up until confirm is in flight. Once the promise is pending nothing closes the dialog, both actions are disabled, and the button shows progress in a slot it already occupied.",
  "A rejected confirm keeps the dialog open, states the reason inline on the risk role, and re-enables the actions, so the second attempt costs one click rather than a re-navigation. The reason is problemMessage with this pattern's fallback: never an HTTP status line, a capability title, or any other machine refusal.",
  "A decision may carry one `alternate`: a narrower version of the same act, outline, beside the confirm. Use it when the dialog would otherwise need a second trigger in the footer behind it for something the rep is deciding right now (send this email, or send it and queue the LinkedIn request). It is never a different object's action, never a third way to say no, and there is never more than one. Cancel remains the only way out.",
  "Destroy wears risk as an outline, never as a fill. CORE 14 has no filled risk surface, and a filled alarm colour on a 32px control is the loudest thing on the page for an action the user has already been warned about twice. Send-on-behalf uses the same dialog and a primary confirm: starting a campaign is irreversible, not destructive, and must not wear the delete colour.",
];

type ConfirmableActionTone = "RISK" | "PRIMARY";

interface ConfirmableActionAlternate {
  label: string;
  icon?: Glyph;
  onConfirm: () => Promise<void>;
}

/** Which button is in flight, so only the pressed one spins. */
type ConfirmableActionRunning = "CONFIRM" | "ALTERNATE";

const RISK_CONFIRM_CLASS = "border-risk text-risk hover:bg-risk-bg";

/** Copy shown when a rejection carries nothing a user could act on. */
const GENERIC_FAILURE = "That did not go through. Nothing was changed.";

function rejectionMessage(reason: unknown): string {
  if (typeof reason === "string" && reason.length > 0) {
    return problemMessage(new Error(reason), GENERIC_FAILURE);
  }
  return problemMessage(reason, GENERIC_FAILURE);
}

interface ConfirmableActionCommon {
  title: string;
  description: ReactNode;
  confirmLabel: string;
  /**
   * Leading glyph on the confirm button. It occupies a fixed slot in every
   * state, so swapping in the spinner cannot move the label.
   */
  confirmIcon?: Glyph;
  /** Resolving closes the dialog; rejecting surfaces the reason inline. */
  onConfirm: () => Promise<void>;
  /**
   * A narrower version of the same decision, outline, beside the confirm. It
   * runs and reports exactly as the confirm does; only one may be offered.
   */
  alternate?: ConfirmableActionAlternate;
  /**
   * RISK is destroy (default). PRIMARY is send-on-behalf: same dialog,
   * no delete colour.
   */
  tone?: ConfirmableActionTone;
}

/**
 * Either the pattern owns its own trigger, or the host mounts it open and takes
 * it down again. Exclusive on purpose: a dialog with two owners is a dialog that
 * reopens itself.
 */
type ConfirmableActionOpening =
  | { trigger: ReactElement<Record<string, unknown>>; onDismiss?: never }
  | { trigger?: never; onDismiss: () => void };

type ConfirmableActionProps = ConfirmableActionCommon & ConfirmableActionOpening;

function confirmVariant(tone: ConfirmableActionTone): "outline" | "primary" {
  switch (tone) {
    case "PRIMARY":
      return "primary";
    case "RISK":
      return "outline";
    default: {
      const exhaustive: never = tone;
      throw new Error(`Unhandled confirm tone: ${String(exhaustive)}`);
    }
  }
}

/**
 * A destructive or irreversible action behind one explicit confirmation.
 */
function ConfirmableAction(props: ConfirmableActionProps) {
  const {
    title,
    description,
    confirmLabel,
    confirmIcon = Trash2,
    alternate,
    onConfirm,
    tone = "RISK",
    trigger,
    onDismiss,
  } = props;

  const [ownOpen, setOwnOpen] = useState(false);
  const [running, setRunning] = useState<ConfirmableActionRunning | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const pendingRef = useRef(false);
  const pending = running !== null;

  /* Mounted by the host is mounted open: there is no trigger left to press. */
  const hosted = trigger === undefined;
  const open = hosted || ownOpen;

  function close(): void {
    setFailure(null);
    if (hosted) {
      onDismiss?.();
      return;
    }
    setOwnOpen(false);
  }

  function handleOpenChange(next: boolean): void {
    /* In flight, Escape, the overlay and the corner close are all inert. */
    if (pendingRef.current) return;
    if (!next) {
      close();
      return;
    }
    setOwnOpen(true);
  }

  async function run(
    which: ConfirmableActionRunning,
    act: () => Promise<void>
  ): Promise<void> {
    if (pendingRef.current) return;
    pendingRef.current = true;
    setRunning(which);
    setFailure(null);
    try {
      await act();
      pendingRef.current = false;
      setRunning(null);
      close();
    } catch (reason) {
      pendingRef.current = false;
      setRunning(null);
      setFailure(rejectionMessage(reason));
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (pendingRef.current) return;
    void run("CONFIRM", onConfirm);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={handleOpenChange}
      disablePointerDismissal={pending}
    >
      {trigger ? <DialogTrigger render={trigger} /> : null}
      <DialogContent showCloseButton={!pending} aria-busy={pending}>
        <Stack render={<form onSubmit={handleSubmit} />} gap="lg">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
          </DialogHeader>

          {failure === null ? null : (
            <Inline
              role="alert"
              gap="sm"
              align="start"
              className="text-label text-risk"
            >
              <Icon icon={AlertTriangle} size="sm" />
              <Box render={<span />}>{failure}</Box>
            </Inline>
          )}

          <DialogFooter>
            <DialogClose
              render={<Button variant="ghost" type="button" disabled={pending} />}
            >
              Cancel
            </DialogClose>
            {alternate === undefined ? null : (
              <Button
                type="button"
                variant="outline"
                disabled={pending}
                onClick={() => void run("ALTERNATE", alternate.onConfirm)}
              >
                <Icon
                  icon={
                    running === "ALTERNATE"
                      ? Loader2
                      : (alternate.icon ?? confirmIcon)
                  }
                  className={running === "ALTERNATE" ? "animate-spin" : undefined}
                />
                {alternate.label}
              </Button>
            )}
            <Button
              type="submit"
              variant={confirmVariant(tone)}
              disabled={pending}
              className={tone === "RISK" ? RISK_CONFIRM_CLASS : undefined}
            >
              <Icon
                icon={running === "CONFIRM" ? Loader2 : confirmIcon}
                className={running === "CONFIRM" ? "animate-spin" : undefined}
              />
              {confirmLabel}
            </Button>
          </DialogFooter>
        </Stack>
      </DialogContent>
    </Dialog>
  );
}

export { ConfirmableAction, CONFIRMABLE_ACTION_RULES };
export type {
  ConfirmableActionAlternate,
  ConfirmableActionProps,
  ConfirmableActionTone,
};
