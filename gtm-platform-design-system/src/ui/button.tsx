"use client";

/*
 * Button, retokenized onto CORE 14.
 *
 * Four roles, one primary per surface. Geometry comes from the density ladder
 * (32 control / 28 compact) and the soft radius ladder (14 control / 12
 * compact). State feedback is immediate, including keyboard activation.
 *
 * LOADING IS ANATOMY, NOT A CALL SITE. Every async surface in a GTM tool -- Send,
 * Save, Confirm, Shuffle -- needs the same three things at once: the control
 * stops accepting input, it says so to assistive tech, and it shows that work is
 * running. Left to the call sites that becomes three different compositions of
 * `disabled`, a hand-placed spinner and a swapped label, so it lives here: one
 * `loading` prop sets `disabled` and `aria-busy` together and swaps the content
 * for the spinner.
 *
 * The swap does not move anything. Label and spinner are stacked in ONE
 * `inline-grid` cell (`col-start-1 row-start-1`), with the label kept in the
 * flow as an `invisible aria-hidden` ghost, so the button keeps the width it had
 * before the click. This is the mechanical form of the rule the queue row states
 * in prose -- reserve the lane, fill it conditionally -- and it is worth the two
 * extra spans: a Send button that narrows by 30px the instant it is pressed
 * shifts every control to its right, which is a layout animation nobody asked
 * for and the motion law forbids.
 *
 * DEPTH (thinned 2026-08-06 against fluid-functionalism). A solid button is the
 * pressable case the elevation system exists for, so primary and secondary sit
 * one whisper above their substrate (`shadow-control`) and settle flat on
 * `:active`, without moving the control or delaying keyboard feedback.
 *
 * Only primary lifts one more under the pointer (`shadow-raised-hover`).
 * Secondary keeps the rest whisper and lets opacity + pressed depth do the
 * feedback -- a second drop stack on every quiet action was what made the
 * toolbar read thicker than fluid. Outline owns its edge with
 * `border-line-strong` and grows no shadow: a hairline plus a drop is two
 * edges, which is the exact dirty outline the light-mode elevation ruling
 * already forbade on the rung itself. Ghost stays flat in every state,
 * because ghost is the role that is deliberately not an object.
 *
 * The rungs come from `<Elevated>`'s ladder (src/components/ui/elevated.tsx).
 * The classes are written here rather than computed because a button's depth is
 * a property of the role, not of what it landed on: a primary in a dialog is
 * the same object as a primary on the page.
 */

import { Button as ButtonPrimitive } from "@base-ui/react/button";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "./cn";
import { Spinner } from "./spinner";

/* One grid cell, shared by the label and the spinner that replaces it. */
const STACKED_CELL_CLASS = "col-start-1 row-start-1 inline-flex items-center gap-1.5";

const buttonVariants = cva(
  "group/button inline-flex shrink-0 items-center justify-center gap-1.5 border border-transparent bg-clip-padding font-medium whitespace-nowrap outline-none select-none focus-visible:ring-2 focus-visible:ring-primary disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        primary:
          "bg-primary text-primary-ink shadow-control hover:opacity-90 hover:shadow-raised-hover active:shadow-none",
        secondary:
          "bg-secondary text-secondary-ink shadow-control hover:opacity-90 active:shadow-none",
        outline:
          "border-line-strong bg-panel text-ink hover:bg-hover aria-expanded:bg-hover",
        ghost: "text-ink hover:bg-hover aria-expanded:bg-hover",
      },
      size: {
        control: "h-control rounded-control px-3 text-label",
        compact: "h-control-sm rounded-compact px-2.5 text-label",
        /* `sm` is a compatibility alias for `compact`, kept for vendored callers. */
        sm: "h-control-sm rounded-compact px-2.5 text-label",
        icon: "h-control w-control rounded-control",
        "icon-sm": "h-control-sm w-control-sm rounded-compact",
      },
    },
    defaultVariants: {
      variant: "primary",
      size: "control",
    },
  }
);

interface ButtonProps
  extends ButtonPrimitive.Props,
    VariantProps<typeof buttonVariants> {
  /** Work is in flight: the control locks, announces itself busy, and spins. */
  loading?: boolean;
}

function Button({
  className,
  variant = "primary",
  size = "control",
  loading = false,
  disabled = false,
  children,
  ...props
}: ButtonProps) {
  return (
    <ButtonPrimitive
      data-slot="button"
      data-loading={loading ? "" : undefined}
      aria-busy={loading ? true : undefined}
      disabled={disabled || loading}
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    >
      {loading ? (
        <span data-slot="button-loading" className="inline-grid place-items-center">
          <span aria-hidden className={cn(STACKED_CELL_CLASS, "invisible")}>
            {children}
          </span>
          <span className={cn(STACKED_CELL_CLASS, "justify-center")}>
            <Spinner />
          </span>
        </span>
      ) : (
        children
      )}
    </ButtonPrimitive>
  );
}

export { Button, buttonVariants };
export type { ButtonProps };
