import type { OpenPullRequest } from "@/lib/api"
import type { InboxSnooze } from "@/features/agents/lib/inboxSnoozes"
import type { AgentThread, ReviewPageRef } from "@/features/agents/lib/types"

export type InboxSplit = "all" | "agents" | "reviews" | "snoozed"

/** How loudly a row's reason reads: a failure, a decision you owe, or news. */
export type InboxTone = "error" | "attention" | "info"

export const INBOX_SPLITS: ReadonlyArray<{ value: InboxSplit; label: string }> =
  [
    { value: "all", label: "All" },
    { value: "agents", label: "Agents" },
    { value: "reviews", label: "Reviews" },
    { value: "snoozed", label: "Snoozed" },
  ]

interface InboxItemBase {
  key: string
  title: string
  subtitle: string
  reason: string
  tone: InboxTone
  updatedAt: number
  /** Set while the item is snoozed: when it comes back on its own. */
  snoozedUntil?: number
  /** Set once a snooze has ended: its time came, or the item changed first. */
  backFromSnooze?: "time" | "activity"
}

export interface ThreadInboxItem extends InboxItemBase {
  kind: "thread"
  thread: AgentThread
}

export interface ReviewInboxItem extends InboxItemBase {
  kind: "review"
  review: ReviewPageRef
  pr: OpenPullRequest
}

export type InboxItem = ThreadInboxItem | ReviewInboxItem

function threadReason(thread: AgentThread): {
  reason: string
  tone: InboxTone
} {
  if (thread.status === "error") return { reason: "Run failed", tone: "error" }
  if (thread.planStatus === "ready" || thread.planStatus === "shared")
    return { reason: "Plan ready for approval", tone: "attention" }
  if (thread.status === "interrupted")
    return { reason: "Waiting for your input", tone: "attention" }
  if (!thread.viewed) return { reason: "New activity", tone: "info" }
  return { reason: "Waiting on you", tone: "info" }
}

/** A thread belongs in the inbox once its agent has stopped and it isn't archived. */
function waitsOnYou(thread: AgentThread): boolean {
  return thread.status !== "running" && thread.resolved !== true
}

function threadItem(thread: AgentThread): ThreadInboxItem {
  return {
    kind: "thread",
    key: `cloud:${thread.id}`,
    title: thread.title,
    subtitle: thread.repoFullName.trim() || "No repository",
    updatedAt: thread.updatedAt,
    thread,
    ...threadReason(thread),
  }
}

function reviewItem(pr: OpenPullRequest): ReviewInboxItem {
  const [owner = "", repo = ""] = pr.repo.split("/")
  return {
    kind: "review",
    key: `review:${pr.repo}#${pr.number}`.toLowerCase(),
    title: pr.title,
    subtitle: `${pr.repo}#${pr.number}`,
    reason: "Review requested",
    tone: "attention",
    updatedAt: pr.updatedAt ? Date.parse(pr.updatedAt) : 0,
    review: { owner, repo, number: pr.number },
    pr,
  }
}

/**
 * A snoozed item stays hidden until its time comes or it changes, whichever is
 * first; either way it comes back sorted as if it had just happened.
 */
function withSnooze(
  item: InboxItem,
  snooze: InboxSnooze | undefined,
  now: number
): InboxItem {
  if (!snooze) return item
  if (item.updatedAt > snooze.snoozed_at_ms)
    return { ...item, backFromSnooze: "activity" }
  if (snooze.until_ms > now) return { ...item, snoozedUntil: snooze.until_ms }
  return {
    ...item,
    backFromSnooze: "time",
    updatedAt: Math.max(item.updatedAt, snooze.until_ms),
  }
}

/** Everything waiting on the user, most recently active first. */
export function buildInbox({
  threads,
  reviews,
  done,
  snoozes,
  now,
}: {
  threads: ReadonlyArray<AgentThread>
  reviews: ReadonlyArray<OpenPullRequest>
  done: ReadonlySet<string>
  snoozes: ReadonlyArray<InboxSnooze>
  now: number
}): Array<InboxItem> {
  const snoozeByKey = new Map(snoozes.map((snooze) => [snooze.key, snooze]))
  return [
    ...threads.filter(waitsOnYou).map(threadItem),
    ...reviews.map(reviewItem),
  ]
    .filter((item) => !done.has(item.key))
    .map((item) => withSnooze(item, snoozeByKey.get(item.key), now))
    .sort((left, right) => right.updatedAt - left.updatedAt)
}

export function inSplit(item: InboxItem, split: InboxSplit): boolean {
  if (split === "snoozed") return item.snoozedUntil !== undefined
  if (item.snoozedUntil !== undefined) return false
  if (split === "agents") return item.kind === "thread"
  if (split === "reviews") return item.kind === "review"
  return true
}
