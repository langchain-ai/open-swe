import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { api, type SlackChannelDirectory } from "@/lib/api"

export const slackChannelDirectoryKey = ["slackChannels"] as const

export const SLACK_CHANNEL_ID_PATTERN = /^[CG][A-Z0-9]{8,}$/

export const slackChannelHref = (id: string): string =>
  `https://slack.com/app_redirect?channel=${encodeURIComponent(id)}`

/** The channels the bot can see; a directory to browse, refreshed on its own. */
export function useSlackChannelDirectory(enabled: boolean) {
  const queryClient = useQueryClient()
  const refresh = useMutation({
    mutationFn: () => api.listSlackChannels(true),
    onSuccess: (data) =>
      queryClient.setQueryData(slackChannelDirectoryKey, data),
    onError: () =>
      toast.error("Could not refresh channels from Slack. Try again."),
  })
  const directory = useQuery({
    queryKey: slackChannelDirectoryKey,
    queryFn: () => api.listSlackChannels(),
    enabled,
    staleTime: 60_000,
  })
  return {
    data: directory.data,
    error: directory.error,
    isLoading: directory.isLoading,
    isError: directory.isError,
    refresh: refresh.mutate,
    isRefreshing: refresh.isPending,
  }
}

/** `#name` for a channel the directory knows, else the id as stored. */
export function slackChannelLabel(
  directory: SlackChannelDirectory | undefined,
  id: string
): string {
  const channel = directory?.channels.find((entry) => entry.id === id)
  return channel ? `#${channel.name}` : id
}

/** The canonical id for a pasted channel id, or null when it cannot be one. */
export function normalizeSlackChannelId(raw: string): string | null {
  const value = raw.trim().toUpperCase()
  return SLACK_CHANNEL_ID_PATTERN.test(value) ? value : null
}

export interface SlackChannelTrigger {
  query: string
  rangeStart: number
  rangeEnd: number
}

/** The `#query` token ending at the cursor; `#123` stays an issue reference. */
export function detectSlackChannelTrigger(
  text: string,
  cursor: number
): SlackChannelTrigger | null {
  const end = Math.max(0, Math.min(text.length, cursor))
  const start = text.slice(0, end).search(/\S*$/)
  const token = text.slice(start, end)
  if (!token.startsWith("#")) return null
  const query = token.slice(1)
  if (/^\d+$/.test(query)) return null
  return { query, rangeStart: start, rangeEnd: end }
}

/** Slack's own mrkdwn reference, which both Slack and the agent resolve by id. */
export function slackChannelReference(id: string, name: string): string {
  return `<#${id}|${name}>`
}
