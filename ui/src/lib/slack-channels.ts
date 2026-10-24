import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useSyncExternalStore } from "react"
import { toast } from "sonner"

import {
  api,
  type SlackChannelDirectory,
  type SlackChannelOption,
} from "@/lib/api"
import { invalidationTopic } from "@/lib/invalidations/topics"

export const slackChannelDirectoryKey = ["slackChannels"] as const

export const SLACK_CHANNEL_ID_PATTERN = /^[CG][A-Z0-9]{8,}$/

export const slackChannelHref = (id: string): string =>
  `https://slack.com/app_redirect?channel=${encodeURIComponent(id)}`

/**
 * The channels the bot can see, as the backend last walked them from Slack.
 * A refresh queues a walk; the directory reloads when the walk's invalidation arrives.
 */
export function useSlackChannelDirectory(enabled: boolean) {
  const queryClient = useQueryClient()
  const refresh = useMutation({
    mutationFn: () => api.refreshSlackChannels(),
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
    meta: { invalidatedBy: [invalidationTopic("slack-channels")] },
  })
  return {
    data: directory.data,
    error: directory.error,
    isLoading: directory.isLoading,
    isError: directory.isError,
    refresh: refresh.mutate,
    isRefreshing: refresh.isPending || directory.data?.refreshing === true,
  }
}

const RECENT_STORAGE_KEY = "open-swe.slack-channels.recent.v1"
const MAX_RECENT = 8
const NO_RECENT: ReadonlyArray<string> = []

let cachedRecentRaw: string | null | undefined
let cachedRecent = NO_RECENT
const recentListeners = new Set<() => void>()

function readRecent(): ReadonlyArray<string> {
  if (typeof window === "undefined") return NO_RECENT
  try {
    const raw = window.localStorage.getItem(RECENT_STORAGE_KEY)
    if (raw !== cachedRecentRaw) {
      cachedRecentRaw = raw
      const parsed: unknown = raw ? JSON.parse(raw) : []
      cachedRecent = Array.isArray(parsed)
        ? parsed
            .filter(
              (id): id is string =>
                typeof id === "string" && SLACK_CHANNEL_ID_PATTERN.test(id)
            )
            .slice(0, MAX_RECENT)
        : NO_RECENT
    }
  } catch {
    cachedRecent = NO_RECENT
  }
  return cachedRecent
}

function onRecentStorage(event: StorageEvent) {
  if (event.key === RECENT_STORAGE_KEY || event.key === null) {
    recentListeners.forEach((listener) => listener())
  }
}

function subscribeRecent(listener: () => void) {
  if (recentListeners.size === 0) {
    window.addEventListener("storage", onRecentStorage)
  }
  recentListeners.add(listener)
  return () => {
    recentListeners.delete(listener)
    if (recentListeners.size === 0) {
      window.removeEventListener("storage", onRecentStorage)
    }
  }
}

/** Move `ids` to the front of this browser's recently chosen channels. */
export function rememberSlackChannels(ids: ReadonlyArray<string>) {
  if (ids.length === 0) return
  const next = [
    ...ids,
    ...readRecent().filter((id) => !ids.includes(id)),
  ].slice(0, MAX_RECENT)
  try {
    const raw = JSON.stringify(next)
    window.localStorage.setItem(RECENT_STORAGE_KEY, raw)
    cachedRecentRaw = raw
    cachedRecent = next
  } catch {
    return
  }
  recentListeners.forEach((listener) => listener())
}

/** The channels this browser chose most recently, newest first. */
export function useRecentSlackChannels(): ReadonlyArray<string> {
  return useSyncExternalStore(subscribeRecent, readRecent, () => NO_RECENT)
}

/**
 * Channels matching `query`, in the order every channel picker offers them:
 * recently chosen first, newest first, then channels the bot is in, then the rest by name.
 */
export function rankSlackChannels(
  channels: ReadonlyArray<SlackChannelOption>,
  query: string,
  recent: ReadonlyArray<string>
): Array<SlackChannelOption> {
  const rank = (channel: SlackChannelOption) => {
    const index = recent.indexOf(channel.id)
    return index >= 0 ? index : recent.length + (channel.is_member ? 0 : 1)
  }
  return channels
    .filter((channel) => slackChannelMatches(channel, query))
    .sort((left, right) => rank(left) - rank(right))
}

/** Name or id match, ignoring a leading `#` the way Slack's switcher does. */
export function slackChannelMatches(
  channel: Pick<SlackChannelOption, "id" | "name">,
  query: string
): boolean {
  const needle = query.trim().replace(/^#/, "").toLowerCase()
  if (!needle) return true
  return (
    channel.name.toLowerCase().includes(needle) ||
    channel.id.toLowerCase().includes(needle)
  )
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
