import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@langchain/macaw-components/Dialog"
import type { ReactNode } from "react"

/** A side panel as a modal sheet on the right edge, for windows too narrow to fit it beside the page. */
export function SideSheet({
  title,
  open,
  onClose,
  children,
}: {
  /** Read by screen readers only; the sheet's content carries its own labels. */
  title: string
  open: boolean
  onClose: () => void
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
        className="inset-y-0 right-0 left-auto h-full max-h-none w-[min(88vw,24rem)] translate-x-0 translate-y-0 rounded-none border-l border-default bg-surface-level-1 shadow-lg"
        childrenClassName="flex-1 gap-0 overflow-hidden p-0"
      >
        <DialogTitle className="sr-only">{title}</DialogTitle>
        {children}
      </DialogContent>
    </Dialog>
  )
}
