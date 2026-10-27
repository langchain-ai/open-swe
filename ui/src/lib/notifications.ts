import type { AgentThread } from "@/features/agents/lib/types"

export const NOTIFICATIONS_PREF_KEY = "open-swe-notifications-enabled"

export function notificationsSupported(): boolean {
  return typeof window !== "undefined" && "Notification" in window
}

export function notificationsEnabled(): boolean {
  if (!notificationsSupported()) return false
  if (Notification.permission !== "granted") return false
  try {
    return localStorage.getItem(NOTIFICATIONS_PREF_KEY) === "true"
  } catch {
    return false
  }
}

export async function requestNotificationPermission(): Promise<NotificationPermission | null> {
  if (!notificationsSupported()) return null
  if (Notification.permission === "granted") return "granted"
  if (Notification.permission === "denied") return "denied"
  return Notification.requestPermission()
}

export function setNotificationsPref(enabled: boolean) {
  try {
    localStorage.setItem(NOTIFICATIONS_PREF_KEY, String(enabled))
  } catch {
    /* ignore */
  }
}

const EXCERPT_CHARS = 180

function plainExcerpt(markdown: string): string {
  const text = markdown
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/<@[A-Z0-9]+>/g, "")
    .replace(/<([^|>]+)\|([^>]+)>/g, "$2")
    .replace(/[*_~`#>]+/g, "")
    .replace(/\s+/g, " ")
    .trim()
  return text.length > EXCERPT_CHARS
    ? `${text.slice(0, EXCERPT_CHARS - 1).trimEnd()}…`
    : text
}

function pullRequestHeadline(
  previous: AgentThread,
  thread: AgentThread
): string | null {
  const pr = thread.pr
  if (!pr) return null
  if (previous.pr?.url !== pr.url) {
    const stats = thread.diffStats
    return stats
      ? `Opened PR #${pr.number} (+${stats.additions} −${stats.deletions})`
      : `Opened PR #${pr.number}`
  }
  if (pr.state === "merged" && previous.pr?.state !== "merged") {
    return `Merged PR #${pr.number}`
  }
  return null
}

export interface RunNotificationContent {
  body: string
  requireInteraction: boolean
}

/** What a run that just ended has to say, or null when it chose to stay quiet. */
export function runNotificationContent(
  previous: AgentThread,
  thread: AgentThread
): RunNotificationContent | null {
  if (thread.status === "error") {
    return {
      body: "Hit an error and stopped. Open it to see what went wrong.",
      requireInteraction: false,
    }
  }
  if (thread.status === "interrupted") {
    return { body: "Stopped before it finished.", requireInteraction: false }
  }
  const reply = thread.lastReply
  if (reply && reply.text === null) return null
  const awaitingChoice = reply?.awaitingChoice === true
  const headline = [
    awaitingChoice ? "Needs your decision" : null,
    pullRequestHeadline(previous, thread),
  ]
    .filter(Boolean)
    .join(" · ")
  const excerpt = reply?.text ? plainExcerpt(reply.text) : ""
  return {
    body:
      [headline, excerpt].filter(Boolean).join("\n") ||
      "Finished. Open it to see the result.",
    requireInteraction: awaitingChoice,
  }
}

export function showRunNotification(
  previous: AgentThread,
  thread: AgentThread,
  onClick: (threadId: string) => void
) {
  if (!notificationsEnabled()) return
  const content = runNotificationContent(previous, thread)
  if (!content) return
  try {
    const n = new Notification(thread.title || "Agent run", {
      body: content.body,
      requireInteraction: content.requireInteraction,
      icon: `${import.meta.env.BASE_URL}logo-mark.png`,
      tag: `run-${thread.id}`,
    })
    n.onclick = () => {
      window.focus()
      onClick(thread.id)
      n.close()
    }
  } catch {
    /* ignore */
  }
}
