import { Dialog } from "@base-ui/react/dialog"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { configureSandboxEnvironment } from "@/lib/api"

export function SandboxEnvironmentDialog({
  threadId,
  open,
  onOpenChange,
}: {
  threadId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [name, setName] = useState("")
  const [value, setValue] = useState("")
  const [acknowledged, setAcknowledged] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const close = () => {
    if (busy) return
    setValue("")
    setName("")
    setAcknowledged(false)
    setError("")
    onOpenChange(false)
  }
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) close()
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/70" />
        <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 w-[min(32rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg border bg-popover p-6 text-popover-foreground shadow-xl">
          <form
            className="flex flex-col gap-4"
            onSubmit={async (event) => {
              event.preventDefault()
              setBusy(true)
              setError("")
              try {
                await configureSandboxEnvironment(
                  threadId,
                  name,
                  value,
                  acknowledged
                )
                setValue("")
                setName("")
                setAcknowledged(false)
                onOpenChange(false)
              } catch {
                setError(
                  "Could not save. Check that you own this cloud thread and the variable is not reserved, then retry."
                )
              } finally {
                setBusy(false)
              }
            }}
          >
            <Dialog.Title className="font-semibold">
              Configure sandbox environment
            </Dialog.Title>
            <Dialog.Description className="text-sm">
              Set an environment variable or secret on the current sandbox.
              Values are write-only in this dashboard, never added to chat.
              Changes apply to new commands, not already-running processes, and
              are lost when the sandbox is replaced.
            </Dialog.Description>
            <label className="flex flex-col gap-1 text-sm">
              Variable name
              <input
                className="rounded border bg-background p-2"
                value={name}
                onChange={(event) => setName(event.target.value)}
                pattern="[A-Za-z_][A-Za-z0-9_]{0,127}"
                required
                disabled={busy}
                autoComplete="off"
                placeholder="SERVICE_API_KEY"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              Value (hidden)
              <input
                type="password"
                className="rounded border bg-background p-2"
                value={value}
                onChange={(event) => setValue(event.target.value)}
                disabled={busy}
                autoComplete="new-password"
              />
            </label>
            <p className="text-sm text-muted-foreground">
              Platform credentials and runtime variables are reserved. Saving an
              existing name overwrites its value. An empty value sets an empty
              variable.
            </p>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={acknowledged}
                onChange={(event) => setAcknowledged(event.target.checked)}
                disabled={busy}
              />
              I understand that all current and future threads, agents, users
              with shell access, and processes sharing this sandbox can read
              these values and may expose them in outputs. This does not change
              the shared workspace image.
            </label>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={close}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={busy || !acknowledged || !name}>
                {busy ? "Saving..." : "Save variable"}
              </Button>
            </div>
          </form>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
