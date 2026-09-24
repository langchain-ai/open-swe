/**
 * Applying sidebar stream events to a paged thread list.
 *
 * Pages keep the item counts they were fetched with, give or take the rows
 * the stream adds or removes; nothing is rebalanced across pages, because the
 * next page is requested by a cursor or offset the loaded pages already fixed.
 */

import type { InfiniteData } from "@tanstack/react-query"
import type { ThreadSortBy, ThreadsPage } from "./api"
import type { AgentThread } from "./types"

export type ThreadIndexListEvent =
  | { type: "upsert"; thread: AgentThread }
  | { type: "remove"; threadId: string }

function sortKey(thread: AgentThread, sort: ThreadSortBy): number {
  return sort === "created_at" ? thread.createdAt : thread.updatedAt
}

/** Whether `a` is listed before `b`: newest first, then by id descending, as the server orders. */
function listedBefore(a: AgentThread, b: AgentThread, sort: ThreadSortBy) {
  const delta = sortKey(a, sort) - sortKey(b, sort)
  return delta !== 0 ? delta > 0 : a.id > b.id
}

function withoutThread<TPageParam>(
  data: InfiniteData<ThreadsPage, TPageParam>,
  threadId: string
): InfiniteData<ThreadsPage, TPageParam> {
  let changed = false
  const pages = data.pages.map((page) => {
    if (!page.items.some((item) => item.id === threadId)) return page
    changed = true
    return { ...page, items: page.items.filter((item) => item.id !== threadId) }
  })
  return changed ? { ...data, pages } : data
}

/**
 * Put `thread` where the server would list it among the loaded rows. A thread
 * older than everything loaded belongs to a page not fetched yet, and is left
 * for that fetch — unless no more pages exist.
 */
function insertThread<TPageParam>(
  data: InfiniteData<ThreadsPage, TPageParam>,
  thread: AgentThread,
  sort: ThreadSortBy
): InfiniteData<ThreadsPage, TPageParam> {
  const pages = [...data.pages]
  for (const [pageIndex, page] of pages.entries()) {
    const position = page.items.findIndex((item) =>
      listedBefore(thread, item, sort)
    )
    if (position === -1) continue
    const items = [...page.items]
    items.splice(position, 0, thread)
    pages[pageIndex] = { ...page, items }
    return { ...data, pages }
  }
  const last = pages.at(-1)
  if (!last || last.hasMore || last.nextCursor) return data
  pages[pages.length - 1] = { ...last, items: [...last.items, thread] }
  return { ...data, pages }
}

/** The list after one stream event; the same object when the event changes nothing. */
export function applyThreadIndexEvent<TPageParam>(
  data: InfiniteData<ThreadsPage, TPageParam>,
  event: ThreadIndexListEvent,
  sort: ThreadSortBy
): InfiniteData<ThreadsPage, TPageParam> {
  if (event.type === "remove") return withoutThread(data, event.threadId)
  const { thread } = event
  const current = data.pages
    .flatMap((page) => page.items)
    .find((item) => item.id === thread.id)
  if (current && sortKey(current, sort) === sortKey(thread, sort)) {
    return replaceThread(data, thread)
  }
  // New, or its sort key moved (an update under `updated_at`): re-place it.
  return insertThread(withoutThread(data, thread.id), thread, sort)
}

/** Swap in `thread` wherever the list holds it, without adding it anywhere. */
export function replaceThread<TPageParam>(
  data: InfiniteData<ThreadsPage, TPageParam>,
  thread: AgentThread
): InfiniteData<ThreadsPage, TPageParam> {
  let changed = false
  const pages = data.pages.map((page) => {
    if (!page.items.some((item) => item.id === thread.id)) return page
    changed = true
    return {
      ...page,
      items: page.items.map((item) => (item.id === thread.id ? thread : item)),
    }
  })
  return changed ? { ...data, pages } : data
}
