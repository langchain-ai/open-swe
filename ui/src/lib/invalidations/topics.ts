/**
 * Topics the backend invalidates when data changes
 * (`openswe/ui_invalidations/topics.py`). A query lists the ones it reads in
 * `meta.invalidatedBy`; an invalidation of any of them refetches it, so it
 * never needs a `refetchInterval`.
 */
type Topic = "workspaces" | "incident-settings"
/** Topics whose records each have one of their own, `<topic>/<key>`. */
type KeyedTopic = "review-styles" | "incidents"

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
