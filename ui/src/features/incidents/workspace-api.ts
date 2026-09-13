import { request } from "./api"
import type { Timestamp } from "./api"

export type DocumentKind = "postmortem" | "status_page_draft"
export interface DocumentRevision {
  kind: DocumentKind
  revision: number
  markdown: string
  author: string
  created_at: Timestamp
  source: string
  run_id: string | null
  evidence: Array<{
    id: string
    source: string
    url: string
    available: boolean
  }>
}
export interface DocumentOperation {
  id: string
  kind: DocumentKind
  status: "pending" | "applied" | "conflict" | "rejected"
  expected_revision: number
  revision: number | null
  error: string | null
  created_at: Timestamp
  updated_at: Timestamp
}
export interface IncidentDocuments {
  incident_id: string
  postmortem: DocumentRevision | null
  status_page_draft: DocumentRevision | null
  operations: DocumentOperation[]
}
const encoded = encodeURIComponent
export const workspaceApi = {
  documents: (id: string) =>
    request<IncidentDocuments>(`/documents/${encoded(id)}`),
  revisions: (id: string, kind: DocumentKind) =>
    request<{ items: DocumentRevision[] }>(
      `/documents/${encoded(id)}/revisions?kind=${kind}`
    ),
  saveDocument: (
    id: string,
    kind: DocumentKind,
    markdown: string,
    expectedRevision: number,
    requestId: string
  ) =>
    request<DocumentOperation>(`/documents/${encoded(id)}/${kind}`, {
      method: "PUT",
      body: JSON.stringify({
        markdown,
        expected_revision: expectedRevision,
        request_id: requestId,
      }),
    }),
  documentOperation: (id: string, operationId: string) =>
    request<DocumentOperation>(
      `/documents/${encoded(id)}/operations/${encoded(operationId)}`
    ),
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
        postmortem_revision: number
      }>
      next_cursor: string | null
    }>(`/documents/history?${params}`)
  },
}
