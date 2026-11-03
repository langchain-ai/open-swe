import { useEffect, useRef } from "react"
import { useStore } from "zustand"
import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import { externalNavigation } from "@/lib/externalNavigation"

export function GitHubNavigationConfirmation() {
  const pending = useStore(externalNavigation.pending)
  const trigger = useRef<HTMLElement | null>(null)

  useEffect(() => {
    const unsubscribe = externalNavigation.pending.subscribe((next) => {
      if (next) trigger.current = next.trigger
    })
    const capture = (event: MouseEvent) => externalNavigation.capture(event)
    document.addEventListener("click", capture, true)
    document.addEventListener("auxclick", capture, true)
    return () => {
      document.removeEventListener("click", capture, true)
      document.removeEventListener("auxclick", capture, true)
      unsubscribe()
      externalNavigation.dismiss()
    }
  }, [])

  return (
    <Dialog
      open={pending !== null}
      onOpenChange={(open) => {
        if (!open) externalNavigation.dismiss()
      }}
    >
      <DialogContent
        title="Are you sure?"
        description="You're about to leave Open SWE and open GitHub."
        showClose={false}
        className="max-w-[calc(100vw-2rem)]"
        onCloseAutoFocus={(event) => {
          event.preventDefault()
          trigger.current?.focus()
        }}
      >
        <p className="text-sm break-all text-secondary">{pending?.url}</p>
        <div className="flex justify-end gap-space-2">
          <Button
            color="secondary"
            variant="outlined"
            size="md"
            onClick={() => externalNavigation.dismiss()}
          >
            Cancel
          </Button>
          <Button size="md" onClick={() => externalNavigation.confirm()}>
            Open GitHub
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
