"use client";

/*
 * Input copy, on CORE 14. The fluid-functionalism item (ANALYSIS.md row 29)
 * rebuilt from our own parts: a read-only field carrying a value nobody types
 * back in -- a Salesforce record id, a thread id, a webhook URL, an API key --
 * plus the one action such a value has.
 *
 * IT IS A COMPOSITION, NOT A NEW CONTROL. `Input` in its readonly state, a
 * ghost icon `Button` in the trailing lane, and `Tooltip`. Nothing here invents
 * a field: an identifier field that measured differently from every other field
 * on a settings page would be a second answer to a question `Input` already
 * answers. The value takes the mono face because mono is for values,
 * identifiers, codes and keys (wiki 02, typography), and the whole field is a
 * copy target so the click lands anywhere on it.
 *
 * THE CONFIRMATION SWAPS IN PLACE. All three glyphs -- copy, check, cross --
 * sit in one `inline-grid` cell (technique 4 from the registry read) and cross
 * fade on opacity and scale at `duration-fast`. Stacking them is what makes the
 * swap free of layout: the button is measured once, by the cell, so the field
 * beside it cannot shift when the glyph changes, no matter which glyph is
 * widest. `motion-reduce` drops the fade and the glyph simply changes.
 *
 * THE ANNOUNCEMENT IS A LIVE REGION, NOT A RENAMED BUTTON. Retitling the button
 * to "Copied" would make a screen reader re-announce the control the user is
 * still sitting on, and the name would then be a lie for anyone arriving later.
 * The button keeps one stable name and a polite `sr-only` region says what
 * happened, once.
 *
 * THE TOOLTIP HAS THREE STATES, AND THAT IS THE POINT OF PORTING IT. `idle` is
 * ordinary hover behaviour. `copied` forces it open so the confirmation is
 * legible without moving the pointer. `suppressed` is the state the reference
 * item exists to teach: after the confirmation times out, the tooltip must NOT
 * spring back to "Copy to clipboard" under a pointer that never moved, so it
 * stays shut until the pointer actually leaves and returns.
 *
 * THE CLIPBOARD HAS A FALLBACK BECAUSE IT IS NOT ALWAYS THERE. `navigator.
 * clipboard` is undefined in an insecure context and rejects under a
 * permissions policy, and both are ordinary conditions for an internal tool on
 * someone's laptop. The `execCommand` textarea is the documented escape, and a
 * failure that reaches past it says so rather than pretending to have worked.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";

import { Button } from "./button";
import { cn } from "./cn";
import { Check, Copy, X } from "./glyphs";
import { Icon } from "./icon";
import { Input } from "./input";
import { Label } from "./label";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "./tooltip";
import { copyText } from "../lib/clipboard";

/** How long the confirmation holds before the control goes back to rest. */
const CONFIRMATION_MS = 2000;

type CopyStatus = "IDLE" | "COPIED" | "FAILED";

const TOOLTIP_LABEL: Record<CopyStatus, string> = {
  IDLE: "Copy to clipboard",
  COPIED: "Copied",
  FAILED: "Copy failed",
};

const ANNOUNCEMENT: Record<CopyStatus, string> = {
  IDLE: "",
  COPIED: "Copied to clipboard",
  FAILED: "Copy failed",
};

/* The trailing lane is the compact control rung, inset inside the field. */
const FIELD_CLASS = "relative flex w-full items-center";
const VALUE_CLASS = "cursor-pointer truncate pr-9 font-mono";
const ACTION_CLASS = "absolute right-0.5";

/* One cell, three glyphs, so the button is measured once and never again. */
const GLYPH_STACK_CLASS = "inline-grid place-items-center";
const GLYPH_CELL_CLASS =
  "col-start-1 row-start-1 transition-[opacity,scale] duration-fast ease-out-quint motion-reduce:transition-none";
const GLYPH_SHOWN_CLASS = "scale-100 opacity-100";
const GLYPH_HIDDEN_CLASS = "scale-75 opacity-0";

interface InputCopyProps {
  /** The value shown and copied. Read-only by construction. */
  value: string;
  /** Names the field above it, and the copy button through it. */
  label?: string;
  /** Fired only when the value actually reached the clipboard. */
  onCopy?: () => void;
  disabled?: boolean;
  className?: string;
}

/**
 * A read-only value with one action: click the field or the button to copy it.
 */
function InputCopy({
  className,
  disabled = false,
  label,
  onCopy,
  value,
}: InputCopyProps) {
  const fieldId = useId();
  const [status, setStatus] = useState<CopyStatus>("IDLE");
  const [open, setOpen] = useState(false);
  const [suppressed, setSuppressed] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
      }
    };
  }, []);

  const copy = useCallback(() => {
    if (disabled) {
      return;
    }

    void copyText(value).then((copied) => {
      setStatus(copied ? "COPIED" : "FAILED");
      setOpen(true);

      if (copied) {
        onCopy?.();
      }

      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
      }

      timerRef.current = setTimeout(() => {
        setStatus("IDLE");
        setOpen(false);
        setSuppressed(true);
      }, CONFIRMATION_MS);
    });
  }, [disabled, onCopy, value]);

  /* `suppressed` is what stops the confirmation reopening under a still pointer. */
  function handleOpenChange(next: boolean) {
    if (next && suppressed) {
      return;
    }

    setOpen(next);
  }

  function releaseSuppression() {
    setSuppressed(false);
  }

  const actionLabel = label === undefined ? "Copy to clipboard" : `Copy ${label}`;

  return (
    <div
      data-slot="input-copy"
      data-status={status}
      className={cn("flex w-full flex-col gap-1.5", className)}
      onPointerLeave={releaseSuppression}
    >
      {label === undefined ? null : <Label htmlFor={fieldId}>{label}</Label>}
      <div className={FIELD_CLASS}>
        <Input
          id={fieldId}
          data-slot="input-copy-value"
          readOnly
          disabled={disabled}
          value={value}
          className={VALUE_CLASS}
          onClick={copy}
        />
        <Tooltip open={open} onOpenChange={handleOpenChange}>
          <TooltipTrigger
            render={
              <Button
                variant="ghost"
                size="icon-sm"
                data-slot="input-copy-action"
                aria-label={actionLabel}
                disabled={disabled}
                className={ACTION_CLASS}
                onClick={copy}
              />
            }
          >
            <span className={GLYPH_STACK_CLASS}>
              <span
                className={cn(
                  GLYPH_CELL_CLASS,
                  status === "IDLE" ? GLYPH_SHOWN_CLASS : GLYPH_HIDDEN_CLASS
                )}
              >
                <Icon icon={Copy} size="sm" />
              </span>
              <span
                className={cn(
                  GLYPH_CELL_CLASS,
                  status === "COPIED" ? GLYPH_SHOWN_CLASS : GLYPH_HIDDEN_CLASS
                )}
              >
                <Icon icon={Check} size="sm" className="text-positive" />
              </span>
              <span
                className={cn(
                  GLYPH_CELL_CLASS,
                  status === "FAILED" ? GLYPH_SHOWN_CLASS : GLYPH_HIDDEN_CLASS
                )}
              >
                <Icon icon={X} size="sm" className="text-risk" />
              </span>
            </span>
          </TooltipTrigger>
          <TooltipContent>{TOOLTIP_LABEL[status]}</TooltipContent>
        </Tooltip>
      </div>
      <span
        data-slot="input-copy-announcement"
        aria-live="polite"
        className="sr-only"
      >
        {ANNOUNCEMENT[status]}
      </span>
    </div>
  );
}

export { InputCopy };
export type { InputCopyProps };
