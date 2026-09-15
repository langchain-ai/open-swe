import { Dialog } from "@base-ui/react/dialog"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"

import { Button } from "@/components/ui/button"
import { ThreadFeedbackCard } from "@/features/agents/components/ThreadFeedbackCard"
import { agentsApi } from "@/features/agents/lib/api"

export function ThreadFeedbackDialog({
  open,
  onOpenChange,
  threadId,
  login,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  threadId: string
  login: string | null
}) {
  const queryClient = useQueryClient()
  const { data, isError, isIdle, isPending, mutate, reset } = useMutation({
    mutationFn: () => agentsApi.openThreadFeedback(threadId),
    onSuccess: (result) => {
      queryClient.setQueryData(["thread-feedback", threadId, login], result)
    },
  })

  useEffect(() => {
    if (open && isIdle) mutate()
    else if (!open && !isIdle) reset()
  }, [isIdle, mutate, open, reset])

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/50 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
        <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg bg-popover p-6 text-popover-foreground shadow-md ring-1 ring-foreground/10 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
          <Dialog.Title className="text-sm font-medium">
            Give feedback
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-xs text-muted-foreground">
            How did Open SWE do on this thread?
          </Dialog.Description>
          {isPending ? (
            <p className="mt-4 text-sm text-muted-foreground">Loading…</p>
          ) : isError ? (
            <div className="mt-4 flex flex-col items-start gap-3">
              <p role="alert" className="text-sm text-destructive">
                Feedback could not be opened. Please try again.
              </p>
              <Button size="sm" onClick={() => mutate()}>
                Try again
              </Button>
            </div>
          ) : data?.status === "ready" ? (
            <ThreadFeedbackCard threadId={threadId} login={login} />
          ) : data ? (
            <p className="mt-4 text-sm text-muted-foreground">
              Feedback has already been recorded for this thread.
            </p>
          ) : null}
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
