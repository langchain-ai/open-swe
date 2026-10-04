/**
 * Topics the backend publishes when data changes (`agent/live/topics.py`).
 * A query lists the ones it reads in `meta.live`; a change to any of them
 * invalidates it, so it never needs a `refetchInterval`.
 */
export const LIVE_TOPICS = {
  workspaces: "workspaces",
} as const

export type LiveTopic = string
