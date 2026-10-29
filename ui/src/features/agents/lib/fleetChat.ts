import { dashboardApiUrl } from "@/lib/dashboard-fetch"

export interface Workspace {
  id: string
  name: string
}
export interface ChatMessage {
  type?: string
  role?: string
  content: string | Array<{ type: string; text?: string }>
}
export interface Thread {
  thread_id: string
  status: string
  metadata?: { thread_title?: string; first_message_preview?: string }
  values?: { messages?: ChatMessage[] }
}

export async function fleetRequest<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(dashboardApiUrl(`/fleet-chat${path}`), {
    credentials: "include",
    ...(body
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : {}),
  })
  if (!response.ok) {
    const error: { detail?: string } = await response.json()
    throw new Error(error.detail ?? "Chat could not be loaded.")
  }
  return response.json()
}
