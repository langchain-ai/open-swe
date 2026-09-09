import { Dialog } from "@base-ui/react/dialog"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Lock } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { agentsApi } from "@/features/agents/lib/api"
import {
  agentThreadKeys,
  invalidateAgentThreadLists,
} from "@/features/agents/lib/queries"
import type { AgentThread } from "@/features/agents/lib/types"
import { useSession } from "@/lib/session"

export function ThreadVisibility({ thread }: { thread: AgentThread }) {
  const session = useSession()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const mutation = useMutation({
    mutationFn: () => agentsApi.makeThreadPrivate(thread.id),
    onSuccess: (updated) => {
      queryClient.setQueryData(agentThreadKeys.detail(thread.id), updated)
      queryClient.setQueryData(
        agentThreadKeys.sidebarActive(thread.id),
        updated
      )
      setOpen(false)
    },
    onSettled: () => invalidateAgentThreadLists(queryClient),
  })
  if (thread.visibility === "private") {
    return (
      <span
        className="inline-flex shrink-0 items-center gap-1 rounded-md bg-muted px-2 py-1 text-xs"
        title="Only you can access this thread. It cannot be made public."
      >
        <Lock className="size-3" />
        Private
      </span>
    )
  }
  if (
    !thread.ownerLogin ||
    thread.ownerLogin.toLowerCase() !== session.data?.login.toLowerCase()
  )
    return null
  const unavailable =
    thread.status === "running" ||
    Boolean(thread.sourceUrl) ||
    Boolean(thread.automationId) ||
    (thread.source && thread.source !== "dashboard") ||
    (thread.origin && thread.origin !== "dashboard")
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!mutation.isPending) {
          setOpen(next)
          mutation.reset()
        }
      }}
    >
      <Dialog.Trigger
        disabled={Boolean(unavailable)}
        title={
          unavailable
            ? "Stop running threads first. Externally linked threads cannot be made private."
            : "Make this thread private"
        }
        data-no-drag=""
        className="shrink-0 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted disabled:opacity-50"
      >
        Make private
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg bg-popover p-6 text-popover-foreground shadow-md ring-1 ring-foreground/10">
          <Dialog.Title className="text-sm font-medium">
            Make thread private?
          </Dialog.Title>
          <Dialog.Description className="mt-3 text-sm text-muted-foreground">
            Only you will be able to access this thread. This cannot be undone:
            private threads cannot be made public. Previously shared copies
            cannot be retracted.
          </Dialog.Description>
          {mutation.error && (
            <p role="alert" className="mt-3 text-xs text-destructive">
              {mutation.error.message}
            </p>
          )}
          <div className="mt-6 flex justify-end gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={mutation.isPending}
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={mutation.isPending || Boolean(unavailable)}
              onClick={() => mutation.mutate()}
            >
              {mutation.isPending ? "Making private..." : "Make private"}
            </Button>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
