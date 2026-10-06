"use client";

/*
 * Dialog, retokenized onto CORE 14.
 *
 * The popup is the outermost containment step, so it takes the shell radius
 * (14px) and its footer strip matches it on the bottom corners. Horizontal
 * centring is `inset-x-4 mx-auto` rather than `left-1/2 -translate-x-1/2`,
 * which keeps the 16px gutter without an arbitrary `max-w-[calc(100%-2rem)]`.
 *
 * DEPTH. `shadow-overlay`, the +4 rung, which this popup was missing outright:
 * it floated over a scrim on a hairline ring alone, so in dark it had no top
 * edge at all against the panel behind it. The overlay rung is the deepest one
 * the ladder has, which is right for the only surface in the product that takes
 * the whole screen out of play while it is open.
 *
 * Enter with a short fade and 95% scale from the centre, then fade on exit.
 * Separate opacity/scale transitions preserve the translate that centres the
 * popup. Reduced motion makes both state changes instant.
 *
 * The backdrop stays `bg-black/10`: a scrim is not a surface, it is a fixed
 * darkening of whatever is behind it, and CORE 14 has no token for that. Add
 * one before reaching for a palette class here.
 *
 * The title dropped its inherited `leading-none`. A dialog spends two rungs:
 * `text-label` and `text-meta`. The header separates them with `gap-2`.
 */

import * as React from "react";
import { Dialog as DialogPrimitive } from "@base-ui/react/dialog";

import { X } from "./glyphs";
import { cn } from "./cn";
import { Button } from "./button";
import { Icon } from "./icon";
import { HELP_CLASS, LABEL_CLASS } from "./label";

function Dialog({ ...props }: DialogPrimitive.Root.Props) {
  return <DialogPrimitive.Root data-slot="dialog" {...props} />;
}

function DialogTrigger({ ...props }: DialogPrimitive.Trigger.Props) {
  return <DialogPrimitive.Trigger data-slot="dialog-trigger" {...props} />;
}

function DialogPortal({ ...props }: DialogPrimitive.Portal.Props) {
  return <DialogPrimitive.Portal data-slot="dialog-portal" {...props} />;
}

function DialogClose({ ...props }: DialogPrimitive.Close.Props) {
  return <DialogPrimitive.Close data-slot="dialog-close" {...props} />;
}

function DialogOverlay({
  className,
  ...props
}: DialogPrimitive.Backdrop.Props) {
  return (
    <DialogPrimitive.Backdrop
      data-slot="dialog-overlay"
      className={cn(
        "fixed inset-0 isolate z-50 bg-black/10 transition-opacity duration-fast ease-out-quint supports-backdrop-filter:backdrop-blur-xs data-starting-style:opacity-0 data-ending-style:opacity-0 motion-reduce:transition-none",
        className
      )}
      {...props}
    />
  );
}

function DialogContent({
  className,
  children,
  showCloseButton = true,
  ...props
}: DialogPrimitive.Popup.Props & {
  showCloseButton?: boolean;
}) {
  return (
    <DialogPortal>
      <DialogOverlay />
      <DialogPrimitive.Popup
        data-slot="dialog-content"
        className={cn(
          "fixed inset-x-4 top-1/2 z-50 mx-auto grid w-auto -translate-y-1/2 gap-4 rounded-shell bg-panel p-4 text-label text-ink shadow-overlay ring-1 ring-line-strong transition-[opacity,scale] duration-fast ease-out-quint outline-none sm:max-w-sm data-starting-style:scale-95 data-starting-style:opacity-0 data-ending-style:opacity-0 motion-reduce:transition-none",
          className
        )}
        {...props}
      >
        {children}
        {showCloseButton ? (
          <DialogPrimitive.Close
            data-slot="dialog-close"
            render={
              <Button
                variant="ghost"
                className="absolute top-2 right-2"
                size="icon-sm"
              />
            }
          >
            <Icon icon={X} />
            <span className="sr-only">Close</span>
          </DialogPrimitive.Close>
        ) : null}
      </DialogPrimitive.Popup>
    </DialogPortal>
  );
}

function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-header"
      className={cn("flex flex-col gap-2", className)}
      {...props}
    />
  );
}

function DialogFooter({
  className,
  showCloseButton = false,
  children,
  ...props
}: React.ComponentProps<"div"> & {
  showCloseButton?: boolean;
}) {
  return (
    <div
      data-slot="dialog-footer"
      className={cn(
        "-mx-4 -mb-4 flex flex-col-reverse gap-2 rounded-b-shell border-t border-line bg-muted p-4 sm:flex-row sm:justify-end",
        className
      )}
      {...props}
    >
      {children}
      {showCloseButton ? (
        <DialogPrimitive.Close render={<Button variant="outline" />}>
          Close
        </DialogPrimitive.Close>
      ) : null}
    </div>
  );
}

function DialogTitle({ className, ...props }: DialogPrimitive.Title.Props) {
  return (
    <DialogPrimitive.Title
      data-slot="dialog-title"
      className={cn(LABEL_CLASS, className)}
      {...props}
    />
  );
}

/*
 * A dialog is a two-rung surface: title is text-label, description is
 * text-meta. Page titles stay on PageFrame.
 */
function DialogDescription({
  className,
  ...props
}: DialogPrimitive.Description.Props) {
  return (
    <DialogPrimitive.Description
      data-slot="dialog-description"
      className={cn(
        HELP_CLASS,
        "*:[a]:underline *:[a]:underline-offset-3 *:[a]:hover:text-ink",
        className
      )}
      {...props}
    />
  );
}

export {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
  DialogTrigger,
};
