import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"

export function DeleteThreadDialog({
  open,
  onOpenChange,
  isDeleting,
  onConfirm,
  detail = "Are you sure you want to delete this thread?",
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  isDeleting: boolean
  onConfirm: () => void
  detail?: string
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="Delete thread"
        description={detail}
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
