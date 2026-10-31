import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"

export function DeleteThreadDialog({
  open,
  onOpenChange,
  threadTitle,
  count = 1,
  isDeleting,
  onConfirm,
  detail = "This cannot be undone.",
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  threadTitle: string
  /** Threads deleted together; more than one names the count, not a title. */
  count?: number
  isDeleting: boolean
  onConfirm: () => void
  detail?: string
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={count > 1 ? `Delete ${count} threads` : "Delete thread"}
        description={`Delete ${count > 1 ? `${count} threads` : `"${threadTitle}"`}? ${detail}`}
        showClose={false}
        className="w-[min(28rem,calc(100vw-2rem))]"
      >
        <div className="flex justify-end gap-space-2">
          <Button
            size="xs"
            color="secondary"
            variant="outlined"
            onClick={() => onOpenChange(false)}
            disabled={isDeleting}
          >
            Cancel
          </Button>
          <Button
            size="xs"
            color="error"
            onClick={onConfirm}
            disabled={isDeleting}
          >
            {isDeleting ? "Deleting..." : "Delete"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
