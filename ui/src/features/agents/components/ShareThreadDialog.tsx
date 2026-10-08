import { Dialog } from "@base-ui/react/dialog"
import { WarningIcon } from "@phosphor-icons/react"
import { useState } from "react"

import { Button } from "@/components/ui/button"

export function ShareThreadDialog({
  open,
  onOpenChange,
  busy,
  running,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  busy: boolean
  running: boolean
  onConfirm: () => void
}) {
  const [acknowledged, setAcknowledged] = useState(false)
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (busy) return
        setAcknowledged(false)
        onOpenChange(next)
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/70" />
        <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 w-[min(32rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-error bg-elevated p-6 text-primary shadow-xl">
          <div className="flex flex-col gap-4">
            <Dialog.Title className="flex items-center gap-2 font-semibold text-error-secondary">
              <WarningIcon className="size-6 shrink-0" weight="fill" />
              Expose this entire thread to the workspace?
            </Dialog.Title>
            <Dialog.Description className="text-sm">
              Everyone with workspace access will be able to read everything
              already in this thread and anything added later: messages, private
              tool results, attachments, plans, and sandbox files. This may
              include secrets or sensitive personal information.
            </Dialog.Description>
            <p className="text-sm font-semibold">
              This cannot be undone. Continuing privately creates a new copy; it
              does not hide this shared thread.
            </p>
            <p className="text-sm text-secondary">
              Private-only tools, personal integrations, and private admin
              capabilities will no longer be available in this thread.
            </p>
            {running && (
              <p role="alert" className="text-sm text-error-secondary">
                Stop the active run before sharing this thread.
              </p>
            )}
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(event) => setAcknowledged(event.target.checked)}
                disabled={busy}
                className="mt-1"
              />
              I understand that everything in this thread becomes visible to the
              workspace.
            </label>
            <div className="mt-2 flex justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  setAcknowledged(false)
                  onOpenChange(false)
                }}
                disabled={busy}
              >
                Keep private
              </Button>
              <Button
                variant="destructive"
                onClick={onConfirm}
                disabled={!acknowledged || busy || running}
              >
                {busy ? "Sharing..." : "Share entire thread"}
              </Button>
            </div>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
