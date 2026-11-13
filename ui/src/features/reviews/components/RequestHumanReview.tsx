import { useState } from "react"
import { IconButton } from "@langchain/macaw-components/IconButton"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@langchain/macaw-components/Popover"
import { Text } from "@langchain/macaw-components/Text"
import { InfoIcon } from "@phosphor-icons/react/dist/ssr/Info"
import { SlackLogoIcon } from "@phosphor-icons/react/dist/ssr/SlackLogo"
import { SlackChannelCombobox } from "@/components/SlackChannelCombobox"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { api, type OpenPullRequest } from "@/lib/api"
import { pullRequestTopic } from "../lib/cache"
import { pullRequestKey } from "../lib/status"
import { PullRequestActionButton } from "./PullRequestActionButton"

/** Posts a review card in the repository's Slack review channel. */
export function RequestHumanReview({ pr }: { pr: OpenPullRequest }) {
  const queryClient = useQueryClient()
  const queryKey = [
    "human-review-availability",
    pr.repo,
    pr.number,
    ...(pr.draft === true ? ["active-only"] : []),
  ]
  const needsReview =
    pr.draft === false &&
    (pr.reviewDecision !== "approved" || pr.reviewRequired)
  const availability = useQuery({
    queryKey,
    enabled: needsReview || pr.draft === true,
    queryFn: () =>
      api.humanReviewAvailability({
        repo: pr.repo,
        number: pr.number,
        draft: pr.draft,
      }),
    meta: { invalidatedBy: [pullRequestTopic(pr)] },
    refetchOnWindowFocus: true,
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
  if (!needsReview && !(pr.draft === true && active)) return null
  const hint = active
    ? permalink
      ? null
      : availability.data?.awaiting_ready
        ? "Only the author can open this review request until its Slack card is posted."
        : "Open SWE could not find this review request in Slack."
    : channel
      ? null
      : availability.isPending
        ? "Checking the default Slack review channel…"
        : availability.isError
          ? "Could not check the default Slack review channel. Choose a review channel manually."
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
      title={hint ?? (active ? "Open the review request in Slack" : undefined)}
    >
      {(needsChannel || (availability.isError && !active)) && (
        <SlackChannelCombobox
          value={channel}
          onValueChange={setChannel}
          placeholder="Choose a review channel"
          aria-label="Review channel"
          size="xs"
          eligible={(option) => option.is_member}
          disabled={requestReview.isPending}
        />
      )}
      <PullRequestActionButton
        label={label}
        icon={active ? SlackLogoIcon : undefined}
        href={active && permalink ? permalink : undefined}
        loading={requestReview.isPending}
        disabled={Boolean(hint)}
        onClick={
          active
            ? undefined
            : () => {
                if (requestReview.isPending) return
                if (hint) toast.error(hint)
                else requestReview.mutate()
              }
        }
      />
      {hint && (
        <Popover>
          <PopoverTrigger asChild>
            <IconButton
              icon={InfoIcon}
              label={
                active
                  ? "Why the review request can't be opened"
                  : "Why a review can't be requested"
              }
              size="xs"
              color="secondary"
              variant="plain"
              tooltipProps={{ title: hint }}
            />
          </PopoverTrigger>
          <PopoverContent align="end" className="w-72 max-w-[calc(100vw-2rem)]">
            <Text variant="xs">{hint}</Text>
          </PopoverContent>
        </Popover>
      )}
    </span>
  )
}
