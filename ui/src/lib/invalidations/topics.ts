/**
 * Topics the backend invalidates when data changes
 * (`agent/ui_invalidations/topics.py`). A query lists the ones it reads in
 * `meta.invalidatedBy`; an invalidation of any of them refetches it, so it
 * never needs a `refetchInterval`.
 */
type TopicName =
  | "workspaces"
  | "review-styles"
  | "incidents"
  | "incident-settings"

export type InvalidationTopic = string

/** A topic, or with `key` the one record under it. */
export function invalidationTopic(
  name: TopicName,
  key?: string
): InvalidationTopic {
  return key === undefined ? name : `${name}/${key}`
}
