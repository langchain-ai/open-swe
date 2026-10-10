import { WarningRegularIcon } from "@langchain/macaw-components/icons"
import { Button } from "@langchain/macaw-components/Button"
import { Checkbox } from "@langchain/macaw-components/Checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
} from "@langchain/macaw-components/Dialog"
import { useState } from "react"

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
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return
        setAcknowledged(false)
        onOpenChange(next)
      }}
    >
      <DialogContent
        title="Expose this entire thread to the workspace?"
        titleIcon={WarningRegularIcon}
        titleIconIntent="error"
        showClose={false}
        className="w-[min(32rem,calc(100vw-2rem))] border border-error"
      >
        <DialogDescription className="text-sm text-primary">
          Everyone with workspace access will be able to read everything already
          in this thread and anything added later: messages, private tool
          results, attachments, plans, and sandbox files. This may include
          secrets or sensitive personal information.
        </DialogDescription>
        <p className="text-sm font-semibold text-primary">
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
        <Checkbox
          checked={acknowledged}
          onCheckedChange={(checked) => setAcknowledged(checked === true)}
          disabled={busy}
          label="I understand that everything in this thread becomes visible to the workspace."
          containerClassName="items-start"
        />
        <div className="flex justify-end gap-space-2">
          <Button
            color="secondary"
            variant="outlined"
            onClick={() => {
              setAcknowledged(false)
              onOpenChange(false)
            }}
            disabled={busy}
          >
            Keep private
          </Button>
          <Button
            color="error"
            onClick={onConfirm}
            disabled={!acknowledged || busy || running}
          >
            {busy ? "Sharing..." : "Share entire thread"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
