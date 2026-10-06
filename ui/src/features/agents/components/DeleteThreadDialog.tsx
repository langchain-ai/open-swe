import { Dialog } from "@base-ui/react/dialog"

import { Button } from "@langchain/gtm-platform-design-system/ui/button"

export function DeleteThreadDialog({
  open,
  onOpenChange,
  threadTitle,
  isDeleting,
  onConfirm,
  detail = "This cannot be undone.",
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  threadTitle: string
  isDeleting: boolean
  onConfirm: () => void
  detail?: string
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/50 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
        <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-compact bg-panel p-6 text-ink shadow-popup ring-1 ring-ink/10 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
          <div className="flex flex-col gap-4">
            <Dialog.Title className="text-body font-medium">
              Delete thread
            </Dialog.Title>
            <Dialog.Description className="text-meta text-ink-subtle">
              Delete "{threadTitle}"? {detail}
            </Dialog.Description>
            <div className="mt-2 flex justify-end gap-2">
              <Button
                variant="outline"
                size="compact"
                onClick={() => onOpenChange(false)}
                disabled={isDeleting}
              >
                Cancel
              </Button>
              <Button
                variant="destructive"
                size="compact"
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
