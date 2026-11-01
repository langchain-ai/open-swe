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

interface DesktopNotificationOptions {
  body: string
  tag: string
  onClick: () => void
  onShow?: () => void
}

/** A native OS notification, shown only when this browser has opted in. */
export class DesktopNotification {
  static show(title: string, options: DesktopNotificationOptions): boolean {
    if (!notificationsEnabled()) return false
    try {
      const notification = new Notification(title, {
        body: options.body,
        icon: `${import.meta.env.BASE_URL}logo-mark.png`,
        tag: options.tag,
      })
      notification.onshow = () => options.onShow?.()
      notification.onclick = () => {
        window.focus()
        options.onClick()
        notification.close()
      }
      return true
    } catch (error) {
      console.warn("Could not show desktop notification", error)
      return false
    }
  }
}

export function showRunNotification(
  thread: AgentThread,
  onClick: (threadId: string) => void
) {
  const statusLabel =
    thread.status === "error"
      ? "encountered an error"
      : thread.status === "interrupted"
        ? "was interrupted"
        : "finished"
  DesktopNotification.show(thread.title || "Agent run", {
    body: `Run ${statusLabel}.`,
    tag: `run-${thread.id}`,
    onClick: () => onClick(thread.id),
  })
}
