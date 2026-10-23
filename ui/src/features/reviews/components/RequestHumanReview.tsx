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
  const queryClient = useQueryClient()
  const queryKey = ["human-review-availability", pr]
  const availability = useQuery({
    queryKey,
    queryFn: () => api.humanReviewAvailability(pr),
    enabled: pr.reviewDecision !== "approved",
    staleTime: 60_000,
  })
  const [channel, setChannel] = useState<string | null>(null)
  const active = availability.data?.active ?? false
  const permalink = availability.data?.permalink ?? ""
  const needsChannel =
    availability.isSuccess && !availability.data.available && !active
  const blockers = availability.data?.blockers ?? []
  const requestReview = useMutation({
    mutationFn: () =>
      api.requestHumanReview(pr, needsChannel ? (channel ?? "") : ""),
    meta: {
      errorTitle: `Could not request a review of ${pullRequestKey(pr)}`,
    },
    onSuccess: async (result) => {
      await queryClient.cancelQueries({ queryKey })
      queryClient.setQueryData<
        Awaited<ReturnType<typeof api.humanReviewAvailability>>
      >(queryKey, (previous) =>
        previous
          ? {
              ...previous,
              active: true,
              permalink: result.permalink,
              blockers: [],
            }
          : previous
      )
      if (result.reused) return
      toast.success(
        `Asked Slack to review ${pullRequestKey(pr)}`,
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
  if (availability.isError && !availability.data)
    return (
      <PullRequestActionButton
        label="Retry review status"
        disabled={availability.isFetching}
        onClick={() => void availability.refetch()}
      />
    )
  const disabledReason = blockers.length
    ? `A review can't be requested yet: ${blockers.join("; ")}.`
    : availability.isPending
      ? "Checking whether this pull request can be reviewed…"
      : requestReview.isPending
        ? "Posting the review request in Slack…"
        : needsChannel && !channel
          ? "This repository has no review channel (reviewChannel in .open-swe/settings.json). Choose one to post the request in."
          : active && !permalink
            ? "A review is already requested, but its Slack message link is unavailable."
            : null
  const label = requestReview.isPending
    ? "Requesting…"
    : active
      ? "Review requested"
      : requestReview.isError
        ? "Retry review request"
        : "Request review in Slack"
  return (
    <span
      className="inline-flex items-center gap-space-2"
      onClick={(event) => event.stopPropagation()}
      title={disabledReason ?? undefined}
    >
      {needsChannel && !blockers.length && !requestReview.isSuccess && (
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
        loading={requestReview.isPending || availability.isPending}
        disabled={disabledReason !== null}
        onClick={() => {
          if (active) {
            if (permalink)
              window.open(permalink, "_blank", "noopener,noreferrer")
          } else {
            requestReview.mutate()
          }
        }}
      />
      {disabledReason && (
        <Popover>
          <PopoverTrigger asChild>
            <IconButton
              icon={InfoIcon}
              label="Why a review can't be requested"
              size="xs"
              color="secondary"
              variant="plain"
              tooltipProps={{ title: disabledReason }}
            />
          </PopoverTrigger>
          <PopoverContent align="end" className="w-72 max-w-[calc(100vw-2rem)]">
            <Text variant="xs">{disabledReason}</Text>
          </PopoverContent>
        </Popover>
      )}
    </span>
  )
}
