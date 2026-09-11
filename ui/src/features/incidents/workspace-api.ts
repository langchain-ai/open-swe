import { request } from "./api"
import type { Timestamp } from "./api"

export type Capability =
  | "supported"
  | "unsupported"
  | "permission_denied"
  | "unavailable"
export interface ProviderSnapshot {
  title: string
  status: { id: string; name: string } | null
  severity: { id: string; name: string } | null
  url: string | null
  postmortem: string | null
}
export interface ProviderState {
  default_connection_name: string | null
  binding: {
    provider: string
    connection_name: string
    external_id: string
    url: string | null
  } | null
  snapshot: ProviderSnapshot | null
  capabilities: Record<string, Capability>
  status_options: Array<{ id: string; name: string }>
  severity_options: Array<{ id: string; name: string }>
  configuration_error: string | null
  configuration_error_kind: string | null
  last_synced_at: Timestamp | null
  error: string | null
  error_kind: string | null
}
export interface ProviderOperation {
  id: string
  status: "accepted" | "sending" | "succeeded" | "failed" | "unknown"
  error: string | null
}
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
const post = (body: unknown): RequestInit => ({
  method: "POST",
  body: JSON.stringify(body),
})

export const workspaceApi = {
  provider: (id: string) => request<ProviderState>(`/providers/${encoded(id)}`),
  connections: (id: string) =>
    request<{
      items: Array<{ name: string; capabilities: Record<string, Capability> }>
    }>(`/providers/connections?incident_id=${encoded(id)}`),
  providerCommand: (
    id: string,
    action: "attach" | "refresh" | "status",
    body: Record<string, string>
  ) =>
    request<{ command_id: string; status: "accepted" | "duplicate" }>(
      `/providers/${encoded(id)}/${action}`,
      post(body)
    ),
  providerOperation: (id: string, operationId: string) =>
    request<ProviderOperation>(
      `/providers/${encoded(id)}/operations/${encoded(operationId)}`
    ),
  searchProvider: (id: string, connection: string, query: string) =>
    request<{
      external: true
      gaps: string[]
      items: Array<ProviderSnapshot & { external_id: string }>
    }>(
      "/providers/search",
      post({ incident_id: id, connection_name: connection, query })
    ),
  externalIncident: (id: string, connection: string, externalId: string) =>
    request<{ external: true; incident: ProviderSnapshot }>(
      "/providers/external",
      post({
        incident_id: id,
        connection_name: connection,
        external_id: externalId,
      })
    ),
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
