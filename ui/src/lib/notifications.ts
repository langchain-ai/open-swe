import type { AgentThread } from "./agents/types"

export const NOTIFICATIONS_PREF_KEY = "open-swe-notifications-enabled"

export function notificationsSupported(): boolean {
  return typeof window !== "undefined" && "Notification" in window
}

export function getNotificationPermission(): NotificationPermission | null {
  if (!notificationsSupported()) return null
  return Notification.permission
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

export function showRunNotification(thread: AgentThread) {
  if (!notificationsEnabled()) return
  const statusLabel =
    thread.status === "error"
      ? "encountered an error"
      : thread.status === "interrupted"
        ? "was interrupted"
        : "finished"
  const title = thread.title || "Agent run"
  const body = `Run ${statusLabel}.`
  try {
    const n = new Notification(title, {
      body,
      icon: "/logo-mark.png",
      tag: `run-${thread.id}`,
    })
    n.onclick = () => {
      window.focus()
      n.close()
    }
  } catch {
    /* ignore */
  }
}
