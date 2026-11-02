import type { Query } from "@tanstack/react-query"

/**
 * Topics the backend invalidates when data changes
 * (`openswe/ui_invalidations/topics.py`). A query lists the ones it reads in
 * `meta.invalidatedBy`; an invalidation of any of them refetches it, so it
 * never needs a `refetchInterval`.
 */
type Topic =
  | "workspaces"
  | "incident-settings"
  | "announcements"
  | "slack-channels"
/** Topics whose records each have one of their own, `<topic>/<key>`. */
type KeyedTopic =
  | "review-styles"
  | "incidents"
  | "pull-requests"
  | "thread-queues"
  | "thread-browsers"
  | "thread-background-work"
  | "thread-goals"
  | "task-workers"

export type InvalidationTopic = string

export function invalidationTopic(name: Topic | KeyedTopic): InvalidationTopic
export function invalidationTopic(
  name: KeyedTopic,
  key: string
): InvalidationTopic
export function invalidationTopic(
  name: Topic | KeyedTopic,
  key?: string
): InvalidationTopic {
  return key === undefined ? name : `${name}/${key}`
}

/** The topics a query reads: listed in `meta.invalidatedBy`, or read from its data. */
export function queryTopics(query: Query): readonly InvalidationTopic[] {
  const topics = query.meta?.invalidatedBy
  return typeof topics === "function"
    ? topics(query.state.data)
    : (topics ?? [])
}
