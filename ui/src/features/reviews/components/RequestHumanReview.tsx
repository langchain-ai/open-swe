import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { api, type OpenPullRequest } from "@/lib/api"
import { pullRequestKey } from "../lib/status"
import { PullRequestActionButton } from "./PullRequestActionButton"

/** Why the server would refuse the request, as far as the list's status can tell. */
function refusal(pr: OpenPullRequest): string | null {
  if (pr.mergeable === false || pr.mergeState === "dirty")
    return "Resolve the merge conflicts first"
  if (pr.ci === "failing" && pr.mergeState !== "unstable")
    return "Fix the failing required checks first"
  return null
}

/** Posts a review card in the repository's Slack review channel. */
export function RequestHumanReview({ pr }: { pr: OpenPullRequest }) {
  const blocked = refusal(pr)
  const queryClient = useQueryClient()
  const queryKey = ["humanReviewStatus", pr.repo, pr.number]
  const status = useQuery({
    queryKey,
    queryFn: () => api.humanReviewStatus(pr.repo, pr.number),
    enabled: pr.reviewDecision !== "approved",
    refetchOnMount: "always",
    refetchOnWindowFocus: "always",
  })
  const requestReview = useMutation({
    mutationFn: () => api.requestHumanReview(pr),
    meta: {
      errorTitle: `Could not request a review of ${pullRequestKey(pr)}`,
    },
    onSuccess: async (result) => {
      await queryClient.cancelQueries({ queryKey })
      queryClient.setQueryData(queryKey, { active: true })
      toast.success(
        result.reused
          ? `${pullRequestKey(pr)} already has a review request in Slack`
          : `Asked Slack to review ${pullRequestKey(pr)}`,
        result.permalink
          ? {
              action: {
                label: "Open in Slack",
                onClick: () =>
                  window.open(
                    result.permalink,
                    "_blank",
                    "noopener,noreferrer"
                  ),
              },
            }
          : undefined
      )
    },
    retry: false,
  })
  if (pr.reviewDecision === "approved") return null
  if (status.isPending) return null
  if (!status.data)
    return (
      <PullRequestActionButton
        label="Retry review status"
        disabled={status.isFetching}
        onClick={() => void status.refetch()}
      />
    )
  const label =
    requestReview.isPending || status.data.active
      ? "Review requested"
      : requestReview.isError
        ? "Retry review request"
        : "Request review in Slack"
  return (
    <span title={blocked ?? undefined}>
      <PullRequestActionButton
        label={label}
        disabled={
          blocked !== null || requestReview.isPending || status.data.active
        }
        onClick={() => requestReview.mutate()}
      />
    </span>
  )
}
