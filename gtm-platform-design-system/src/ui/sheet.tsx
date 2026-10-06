"use client";

/*
 * Sheet — floating edge panel on CORE 14.
 *
 * FLOATING, NOT FLUSH. The panel sits on a 16px (`spacing(4)`) gutter from the
 * viewport edge on every side it docks, with `rounded-shell` all around — a
 * sheet that floats over the page, not a drawer welded to the chrome. The
 * gutter matches Dialog's `inset-x-4` so modal and sheet feel like one family.
 *
 * DEPTH. `shadow-overlay` + ONE edge (`ring-1 ring-line-strong`). Same +4 rung
 * as Dialog: a sheet is a dialog docked to an edge. No second edge painted by
 * the shadow.
 *
 * PADDING. The popup itself carries `p-5` / `gap-4`. Header and footer do not
 * restate that inset — call sites that need a full-bleed body (mobile sidebar)
 * pass `p-0` on SheetContent and own their own padding.
 *
 * MOTION. Transition list is `[opacity,translate]` (Tailwind v4 standalone
 * translate). Exit is asymmetric (wiki 02, principle 6): half the entrance
 * travel on the same clock, so dismissal never reads as the opening rewound.
 *
 * The backdrop stays `bg-black/10`; see the note in dialog.tsx.
 */

import * as React from "react";
import { Dialog as SheetPrimitive } from "@base-ui/react/dialog";

import { X } from "./glyphs";
import { cn } from "./cn";
import { Button } from "./button";
import { Icon } from "./icon";

function Sheet({ ...props }: SheetPrimitive.Root.Props) {
  return <SheetPrimitive.Root data-slot="sheet" {...props} />;
}

function SheetTrigger({ ...props }: SheetPrimitive.Trigger.Props) {
  return <SheetPrimitive.Trigger data-slot="sheet-trigger" {...props} />;
}

function SheetClose({ ...props }: SheetPrimitive.Close.Props) {
  return <SheetPrimitive.Close data-slot="sheet-close" {...props} />;
}

function SheetPortal({ ...props }: SheetPrimitive.Portal.Props) {
  return <SheetPrimitive.Portal data-slot="sheet-portal" {...props} />;
}

function SheetOverlay({ className, ...props }: SheetPrimitive.Backdrop.Props) {
  return (
    <SheetPrimitive.Backdrop
      data-slot="sheet-overlay"
      className={cn(
        "fixed inset-0 z-50 bg-black/10 transition-opacity duration-fast ease-out-quint data-ending-style:opacity-0 data-starting-style:opacity-0 motion-reduce:transition-none supports-backdrop-filter:backdrop-blur-xs",
        className
      )}
      {...props}
    />
  );
}

const SHEET_SIDE_CLASS: Record<"top" | "right" | "bottom" | "left", string> = {
  right:
    "inset-y-4 right-4 h-auto w-full max-w-sm data-ending-style:translate-x-5 data-starting-style:translate-x-8",
  left:
    "inset-y-4 left-4 h-auto w-full max-w-sm data-ending-style:-translate-x-5 data-starting-style:-translate-x-8",
  top:
    "inset-x-4 top-4 h-auto data-ending-style:-translate-y-5 data-starting-style:-translate-y-8",
  bottom:
    "inset-x-4 bottom-4 h-auto data-ending-style:translate-y-5 data-starting-style:translate-y-8",
};

function SheetContent({
  className,
  children,
  side = "right",
  showCloseButton = true,
  ...props
}: SheetPrimitive.Popup.Props & {
  side?: "top" | "right" | "bottom" | "left";
  showCloseButton?: boolean;
}) {
  return (
    <SheetPortal>
      <SheetOverlay />
      <SheetPrimitive.Popup
        data-slot="sheet-content"
        data-side={side}
        className={cn(
          "fixed z-50 flex flex-col gap-4 rounded-shell bg-panel p-5 text-body text-ink shadow-overlay ring-1 ring-line-strong transition-[opacity,translate] duration-fast ease-out-quint outline-none data-ending-style:opacity-0 data-starting-style:opacity-0 motion-reduce:transition-none",
          SHEET_SIDE_CLASS[side],
          className
        )}
        {...props}
      >
        {children}
        {showCloseButton ? (
          <SheetPrimitive.Close
            data-slot="sheet-close"
            render={
              <Button
                variant="ghost"
                className="absolute top-4 right-4"
                size="icon-sm"
              />
            }
          >
            <Icon icon={X} />
            <span className="sr-only">Close</span>
          </SheetPrimitive.Close>
        ) : null}
      </SheetPrimitive.Popup>
    </SheetPortal>
  );
}

function SheetHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sheet-header"
      className={cn("flex flex-col gap-1.5 pr-8", className)}
      {...props}
    />
  );
}

function SheetFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sheet-footer"
      className={cn("mt-auto flex flex-col gap-2", className)}
      {...props}
    />
  );
}

function SheetTitle({ className, ...props }: SheetPrimitive.Title.Props) {
  return (
    <SheetPrimitive.Title
      data-slot="sheet-title"
      className={cn("text-title font-medium text-ink", className)}
      {...props}
    />
  );
}

function SheetDescription({
  className,
  ...props
}: SheetPrimitive.Description.Props) {
  return (
    <SheetPrimitive.Description
      data-slot="sheet-description"
      className={cn("text-body text-ink-subtle", className)}
      {...props}
    />
  );
}

export {
  Sheet,
  SheetTrigger,
  SheetClose,
  SheetContent,
  SheetHeader,
  SheetFooter,
  SheetTitle,
  SheetDescription,
};
