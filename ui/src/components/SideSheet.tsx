import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@langchain/macaw-components/Dialog"
import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

/** A panel as an edge-anchored modal sheet, for windows too narrow to fit it inline. */
export function SideSheet({
  side,
  title,
  description,
  open,
  onClose,
  className,
  children,
}: {
  side: "left" | "right"
  /** Read by screen readers only; the sheet's content carries its own heading. */
  title: string
  description?: string
  open: boolean
  onClose: () => void
  className?: string
  children: ReactNode
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
    >
      <DialogContent
        showClose={false}
        className={cn(
          "inset-y-0 h-full max-h-none translate-x-0 translate-y-0 rounded-none bg-surface-level-1 shadow-lg",
          side === "right"
            ? "right-0 left-auto border-l border-default"
            : "right-auto left-0 border-r border-default",
          className
        )}
        childrenClassName="flex-1 gap-0 overflow-hidden p-0"
      >
        <DialogTitle className="sr-only">{title}</DialogTitle>
        {description && (
          <DialogDescription className="sr-only">
            {description}
          </DialogDescription>
        )}
        {children}
      </DialogContent>
    </Dialog>
  )
}
