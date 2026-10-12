import type { ReactNode } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"

/** Asks before an irreversible action; it can't be dismissed while the action is pending. */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  pendingLabel = confirmLabel,
  pending = false,
  destructive = false,
  onConfirm,
  children,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: ReactNode
  description: ReactNode
  confirmLabel: string
  pendingLabel?: string
  pending?: boolean
  destructive?: boolean
  onConfirm: () => void
  children?: ReactNode
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next || !pending) onOpenChange(next)
      }}
    >
      <DialogContent title={title} description={description} showClose={false}>
        {children}
        <div className="flex justify-end gap-space-2">
          <Button
            color="secondary"
            variant="outlined"
            disabled={pending}
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button
            color={destructive ? "error" : "primary"}
            disabled={pending}
            onClick={onConfirm}
          >
            {pending ? pendingLabel : confirmLabel}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
