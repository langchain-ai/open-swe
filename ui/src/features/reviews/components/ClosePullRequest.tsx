import { useMutation } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { api, type OpenPullRequest } from "@/lib/api"
import { ConfirmCloseDialog } from "./ConfirmCloseDialog"

export function ClosePullRequest({
  pr,
  onClosed,
}: {
  pr: OpenPullRequest
  onClosed: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  const close = useMutation({
    mutationFn: async () => {
      const result = await api.closePullRequest(pr)
      if (!result.closed) throw new Error("GitHub did not confirm the close.")
    },
    onSuccess: () => {
      toast.success(`Closed ${pr.repo}#${pr.number}`)
      onClosed()
    },
    onError: (error) =>
      toast.error(`Could not close ${pr.repo}#${pr.number}`, {
        description: error.message,
      }),
    retry: false,
  })
  return (
    <div>
      <Button
        size="sm"
        variant="outline"
        disabled={close.isPending || close.isSuccess}
        aria-live="polite"
        onClick={() => setConfirming(true)}
      >
        {close.isPending ? "Closing…" : close.isError ? "Retry close" : "Close"}
      </Button>
      {confirming && (
        <ConfirmCloseDialog
          pullRequests={[pr]}
          onCancel={() => setConfirming(false)}
          onConfirm={() => {
            setConfirming(false)
            close.mutate()
          }}
        />
      )}
      {close.error && (
        <p role="alert" className="mt-1 text-destructive">
          {close.error.message}
        </p>
      )}
    </div>
  )
}
