import type { InfiniteData } from "@tanstack/react-query"
import type { ThreadsPage } from "./api"
import type { AgentThread } from "./types"

export interface Replaced<T> {
  value: T
  found: boolean
}

/** `items` with the entry for `thread.id` swapped for `thread`, in place. */
export function replaceThreadInList(
  items: Array<AgentThread>,
  thread: AgentThread
): Replaced<Array<AgentThread>> {
  const index = items.findIndex((item) => item.id === thread.id)
  if (index === -1) return { value: items, found: false }
  const value = [...items]
  value[index] = thread
  return { value, found: true }
}

export function replaceThreadInPage(
  page: ThreadsPage,
  thread: AgentThread
): Replaced<ThreadsPage> {
  const items = replaceThreadInList(page.items, thread)
  return items.found
    ? { value: { ...page, items: items.value }, found: true }
    : { value: page, found: false }
}

export function replaceThreadInPages(
  data: InfiniteData<ThreadsPage>,
  thread: AgentThread
): Replaced<InfiniteData<ThreadsPage>> {
  let found = false
  const pages = data.pages.map((page) => {
    const replaced = replaceThreadInPage(page, thread)
    found ||= replaced.found
    return replaced.value
  })
  return found
    ? { value: { ...data, pages }, found: true }
    : { value: data, found: false }
}

/**
 * A thread's detail with the list summary's fields laid over it. The summary
 * carries no conversation, so what only the detail GET knows is kept.
 */
export function mergeThreadSummary(
  detail: AgentThread,
  summary: AgentThread
): AgentThread {
  return {
    ...detail,
    ...summary,
    messages: detail.messages,
    pendingMessages: detail.pendingMessages,
    changedFiles: detail.changedFiles,
  }
}
