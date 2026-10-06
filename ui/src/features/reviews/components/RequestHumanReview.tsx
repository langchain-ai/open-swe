import { useState } from "react"
import { IconButton } from "@langchain/macaw-components/IconButton"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/macaw-components/Popover"
import { Text } from "@langchain/macaw-components/Text"
import { InfoIcon } from "@phosphor-icons/react/dist/ssr/Info"
import { SlackChannelCombobox } from "@/components/SlackChannelCombobox"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { api, type OpenPullRequest } from "@/lib/api"
import { pullRequestKey } from "../lib/status"
import { PullRequestActionButton } from "./PullRequestActionButton"

/** Posts a review card in the repository's Slack review channel. */
export function RequestHumanReview({ pr }: { pr: OpenPullRequest }) {
  const availability = useQuery({
    queryKey: ["human-review-availability", pr],
    queryFn: () => api.humanReviewAvailability(pr),
    enabled: pr.reviewDecision !== "approved",
    staleTime: 60_000,
  })
  const [channel, setChannel] = useState<string | null>(null)
  const needsChannel = availability.isSuccess && !availability.data.available
  const blockers = availability.data?.blockers ?? []
  const reason = blockers.length
    ? `A review can't be requested yet: ${blockers.join("; ")}.`
    : null
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
    mutationFn: () =>
      api.requestHumanReview(pr, needsChannel ? (channel ?? "") : ""),
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
    <span
      className="inline-flex items-center gap-space-2"
      onClick={(event) => event.stopPropagation()}
      title={reason ?? undefined}
    >
      {needsChannel && !reason && !requestReview.isSuccess && (
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
          reason !== null ||
          availability.isPending ||
          (needsChannel && !channel) ||
          requestReview.isPending ||
          status.data.active
        }
        onClick={() => requestReview.mutate()}
      />
      {reason && (
        <Popover>
          <PopoverTrigger asChild>
            <IconButton
              icon={InfoIcon}
              label="Why a review can't be requested"
              size="xs"
              color="secondary"
              variant="plain"
              tooltipProps={{ title: reason }}
            />
          </PopoverTrigger>
          <PopoverContent align="end" className="w-72 max-w-[calc(100vw-2rem)]">
            <Text variant="xs">{reason}</Text>
          </PopoverContent>
        </Popover>
      )}
    </span>
  )
}
