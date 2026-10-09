import { useState } from "react"
import { SlackChannelCombobox } from "@/components/SlackChannelCombobox"
import { useMutation, useQuery } from "@tanstack/react-query"
import { toast } from "sonner"

import { api, type OpenPullRequest } from "@/lib/api"
import { pullRequestKey } from "../lib/status"
import { PullRequestActionButton } from "./PullRequestActionButton"

/** Whether the server would refuse the request, as far as the list's status can tell. */
function refused(pr: OpenPullRequest): boolean {
  return (
    pr.state !== "open" ||
    pr.draft !== false ||
    pr.mergeable === false ||
    pr.mergeState === "dirty" ||
    (pr.ci === "failing" && pr.mergeState !== "unstable")
  )
}

/** Posts a review card in the repository's Slack review channel. */
export function RequestHumanReview({ pr }: { pr: OpenPullRequest }) {
  const availability = useQuery({
    queryKey: ["human-review-availability", pr],
    queryFn: () => api.humanReviewAvailability(pr),
    enabled: pr.reviewDecision !== "approved" && !refused(pr),
    staleTime: 60_000,
  })
  const [channel, setChannel] = useState<string | null>(null)
  const needsChannel = availability.isSuccess && !availability.data.available
  const requestReview = useMutation({
    mutationFn: () =>
      api.requestHumanReview(pr, needsChannel ? (channel ?? "") : ""),
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
  if (pr.reviewDecision === "approved" || refused(pr)) return null
  const label =
    requestReview.isPending || requestReview.isSuccess
      ? "Review requested"
      : requestReview.isError
        ? "Retry review request"
        : "Request review in Slack"
  return (
    <span
      className="inline-flex items-center gap-2"
      onClick={(event) => event.stopPropagation()}
    >
      {needsChannel && !requestReview.isSuccess && (
        <SlackChannelCombobox
          value={channel}
          onValueChange={setChannel}
          placeholder="Choose a review channel"
          aria-label="Review channel"
          eligible={(option) => option.is_member}
          disabled={requestReview.isPending}
        />
      )}
      <PullRequestActionButton
        label={label}
        disabled={
          !availability.isSuccess ||
          (needsChannel && !channel) ||
          requestReview.isPending ||
          requestReview.isSuccess
        }
        onClick={() => requestReview.mutate()}
      />
    </span>
  )
}
