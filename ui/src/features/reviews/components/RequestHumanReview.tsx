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
    staleTime: 60_000,
  })
  const [channel, setChannel] = useState<string | null>(null)
  const active = availability.data?.active ?? false
  const permalink = availability.data?.permalink ?? ""
  const needsChannel =
    availability.isSuccess && !availability.data.available && !active
  const requestReview = useMutation({
    mutationFn: () => api.requestHumanReview(pr, channel ?? ""),
    meta: {
      errorTitle: `Could not request a review of ${pullRequestKey(pr)}`,
    },
    onSuccess: async (result) => {
      await queryClient.cancelQueries({ queryKey })
      queryClient.setQueryData<
        Awaited<ReturnType<typeof api.humanReviewAvailability>>
      >(queryKey, (previous) => ({
        available: previous?.available ?? true,
        active: true,
        permalink: result.permalink,
        blockers: [],
      }))
      if (result.reused) return
      toast.success(
        `Asked ${result.channel_name ? `#${result.channel_name}` : "Slack"} to review ${pullRequestKey(pr)}`,
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
  if (pr.draft !== false) return null
  const channelHint =
    active || channel
      ? null
      : availability.isPending
        ? "Checking the default Slack review channel…"
        : availability.isError
          ? "Could not check the default Slack review channel. Choose a review channel manually or retry the review status."
          : needsChannel
            ? "No default Slack review channel is configured. Choose a review channel manually to enable this button."
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
      title={channelHint ?? undefined}
    >
      {(needsChannel || (availability.isError && !active)) &&
        !requestReview.isSuccess && (
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
        loading={requestReview.isPending}
        disabled={Boolean(channelHint)}
        onClick={() => {
          if (requestReview.isPending) return
          if (active && permalink) {
            window.open(permalink, "_blank", "noopener,noreferrer")
          } else if (channelHint) {
            toast.error(channelHint)
          } else {
            requestReview.mutate()
          }
        }}
      />
      {availability.isError && (
        <PullRequestActionButton
          label="Retry review status"
          loading={availability.isFetching}
          disabled={availability.isFetching}
          onClick={() => void availability.refetch()}
        />
      )}
      {channelHint && (
        <Popover>
          <PopoverTrigger asChild>
            <IconButton
              icon={InfoIcon}
              label="Why a review can't be requested"
              size="xs"
              color="secondary"
              variant="plain"
              tooltipProps={{ title: channelHint }}
            />
          </PopoverTrigger>
          <PopoverContent align="end" className="w-72 max-w-[calc(100vw-2rem)]">
            <Text variant="xs">{channelHint}</Text>
          </PopoverContent>
        </Popover>
      )}
    </span>
  )
}
