import { request } from "./api"
import type { Timestamp } from "./api"

export const workspaceApi = {
  documents: (id: string) =>
    request<{
      incident_id: string
      postmortem: { markdown: string } | null
    }>(`/documents/${encodeURIComponent(id)}`),
  history: (q: string, cursor?: string) => {
    const params = new URLSearchParams({ q, limit: "25" })
    if (cursor) params.set("cursor", cursor)
    return request<{
      items: Array<{
        id: string
        title: string
        channel_name: string
        status: string
        updated_at: Timestamp
      }>
      next_cursor: string | null
    }>(`/documents/history?${params}`)
  },
}
