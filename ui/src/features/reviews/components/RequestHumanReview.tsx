import { useMutation, useQuery } from "@tanstack/react-query"
import { toast } from "sonner"

import { api, type OpenPullRequest } from "@/lib/api"
import { useProfile } from "@/lib/profile"
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
  const profile = useProfile()
  const availability = useQuery({
    queryKey: ["human-review-availability", pr],
    queryFn: () => api.humanReviewAvailability(pr),
    enabled:
      Boolean(profile.data?.human_review_requests) &&
      pr.reviewDecision !== "approved",
    staleTime: 60_000,
  })
  const blocked = refusal(pr)
  const requestReview = useMutation({
    mutationFn: () => api.requestHumanReview(pr),
    meta: {
      errorTitle: `Could not request a review of ${pullRequestKey(pr)}`,
    },
    onSuccess: (result) => {
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
  if (
    !profile.data?.human_review_requests ||
    pr.reviewDecision === "approved" ||
    !availability.data?.available
  )
    return null
  const label =
    requestReview.isPending || requestReview.isSuccess
      ? "Review requested"
      : requestReview.isError
        ? "Retry review request"
        : "Request review in Slack"
  return (
    <span title={blocked ?? undefined}>
      <PullRequestActionButton
        label={label}
        disabled={
          blocked !== null || requestReview.isPending || requestReview.isSuccess
        }
        onClick={() => requestReview.mutate()}
      />
    </span>
  )
}
