import { useMutation } from "@tanstack/react-query"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { api, type OpenPullRequest } from "@/lib/api"

export function MarkPullRequestReady({
  pr,
  onReady,
}: {
  pr: OpenPullRequest
  onReady: () => void
}) {
  const ready = useMutation({
    mutationFn: async () => {
      const result = await api.markPullRequestReady(pr)
      if (!result.ready)
        throw new Error("GitHub did not confirm the ready for review.")
    },
    onSuccess: () => {
      toast.success(`Marked ${pr.repo}#${pr.number} ready for review`)
      onReady()
    },
    onError: (error) =>
      toast.error(`Could not mark ${pr.repo}#${pr.number} ready`, {
        description: error.message,
      }),
    retry: false,
  })
  return (
    <div>
      <Button
        size="sm"
        variant="outline"
        disabled={ready.isPending || ready.isSuccess}
        aria-live="polite"
        onClick={() => ready.mutate()}
      >
        {ready.isPending
          ? "Marking ready…"
          : ready.isSuccess
            ? "Marked ready"
            : ready.isError
              ? "Retry mark ready"
              : "Mark ready"}
      </Button>
      {ready.error && (
        <p role="alert" className="mt-1 text-destructive">
          {ready.error.message}
        </p>
      )}
    </div>
  )
}
