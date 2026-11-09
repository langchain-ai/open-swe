import { Button, type ButtonProps } from "@langchain/macaw-components/Button"
import { useMutation, useQueryClient } from "@tanstack/react-query"

import { api, type PullRequestPreview, type ReviewDetail } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"
import { useSession } from "@/lib/session"
import {
  PULL_REQUEST_STATUS,
  pullRequestPreviewQuery,
} from "@/features/reviews/lib/cache"
import {
  reviewKeys,
  statusRef,
  type PullRequestRef,
} from "@/features/reviews/lib/reviewKeys"
import { TextPopover } from "./TextPopover"

/** Dismisses an Open SWE finding everywhere the page shows it, before the backend answers. */
function useDismissFinding(pr: PullRequestRef, findingId: string) {
  const queryClient = useQueryClient()
  const login = useSession().data?.login
  const previewKey = pullRequestPreviewQuery(statusRef(pr)).queryKey
  return useMutation({
    mutationFn: (reason: string) =>
      api.dismissFinding(pr.owner, pr.repo, pr.number, findingId, reason),
    meta: { errorTitle: "Couldn't dismiss the finding" },
    onMutate: async (reason) => {
      const by = login ? `Dismissed by @${login}` : "Dismissed"
      const undoDetail = await optimisticUpdate<ReviewDetail>(
        queryClient,
        reviewKeys.detail(pr),
        (current) => ({
          ...current,
          findings: current.findings.map((finding) =>
            finding.id === findingId
              ? {
                  ...finding,
                  status: "dismissed",
                  resolution_note: reason ? `${by}: ${reason}` : by,
                }
              : finding
          ),
        })
      )
      const undoPreview = await optimisticUpdate<PullRequestPreview>(
        queryClient,
        previewKey,
        (current) =>
          current.unresolved
            ? {
                ...current,
                unresolved: current.unresolved.filter(
                  (thread) => thread.finding_id !== findingId
                ),
              }
            : current
      )
      return {
        undo: () => {
          undoDetail()
          undoPreview()
        },
      }
    },
    onError: (_error, _reason, context) => context?.undo(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: previewKey })
      void queryClient.invalidateQueries({ queryKey: [PULL_REQUEST_STATUS] })
    },
  })
}

/** Asks for an optional reason, then dismisses one of Open SWE's findings. */
export function DismissFinding({
  pr,
  findingId,
  size = "sm",
  variant = "plain",
}: {
  pr: PullRequestRef
  findingId: string
  size?: ButtonProps["size"]
  variant?: ButtonProps["variant"]
}) {
  const dismiss = useDismissFinding(pr, findingId)
  return (
    <TextPopover
      trigger={
        <Button
          size={size}
          color="secondary"
          variant={variant}
          disabled={dismiss.isPending}
        >
          Dismiss
        </Button>
      }
      title="Dismiss this finding?"
      description="Open SWE records the dismissal and your reason with its review, and resolves the finding's thread on GitHub."
      placeholder="Reason (optional)"
      submitLabel="Dismiss finding"
      onSubmit={(reason) => dismiss.mutateAsync(reason)}
    />
  )
}
