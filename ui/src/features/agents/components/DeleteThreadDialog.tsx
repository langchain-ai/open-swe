import { Dialog } from "@base-ui/react/dialog"

import { Button } from "@/components/ui/button"

export function DeleteThreadDialog({
  open,
  onOpenChange,
  threadTitle,
  isDeleting,
  onConfirm,
  detail = "This cannot be undone.",
  error,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  threadTitle: string
  isDeleting: boolean
  onConfirm: () => void
  detail?: string
  error?: string | null
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/50 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
        <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg bg-popover p-6 text-popover-foreground shadow-md ring-1 ring-foreground/10 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
          <div className="flex flex-col gap-4">
            <Dialog.Title className="text-sm font-medium">
              Delete thread
            </Dialog.Title>
            <Dialog.Description className="text-xs text-muted-foreground">
              Delete "{threadTitle}"? {detail}
            </Dialog.Description>
            {error && <p className="text-xs text-destructive">{error}</p>}
            <div className="mt-2 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => onOpenChange(false)}
                disabled={isDeleting}
              >
                Cancel
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={onConfirm}
                disabled={isDeleting}
              >
                {isDeleting ? "Deleting..." : "Delete"}
              </Button>
            </div>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
