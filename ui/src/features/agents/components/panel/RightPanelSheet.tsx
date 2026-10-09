import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@langchain/macaw-components/Dialog"
import type { ReactNode } from "react"

import { RIGHT_PANEL_SHEET_CLASS_NAME } from "@/features/agents/components/panel/rightPanelLayout"
import { cn } from "@/lib/utils"

/** The work panel as a right-anchored modal sheet, for windows too narrow to fit it inline. */
export function RightPanelSheet(props: {
  children: ReactNode
  open: boolean
  onClose: () => void
}) {
  return (
    <Dialog
      open={props.open}
      onOpenChange={(open) => {
        if (!open) props.onClose()
      }}
    >
      <DialogContent
        showClose={false}
        className={cn(
          "inset-y-0 right-0 left-auto h-full max-h-none translate-x-0 translate-y-0 rounded-none border-l border-default bg-surface-level-1 shadow-lg",
          RIGHT_PANEL_SHEET_CLASS_NAME
        )}
        childrenClassName="flex-1 gap-0 overflow-hidden p-0"
      >
        <DialogTitle className="sr-only">Work panel</DialogTitle>
        <DialogDescription className="sr-only">
          Changes, terminals, and files for this thread.
        </DialogDescription>
        {props.children}
      </DialogContent>
    </Dialog>
  )
}
