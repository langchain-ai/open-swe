/**
 * Shared plumbing for the dashboard backend clients.
 *
 * Every feature owns its own client object; they all go through `request` so a
 * failure from any of them is one `ApiError` type. Requests are sent with
 * credentials so the httpOnly `osw_session` cookie set by the OAuth callback
 * rides along on cross-origin calls.
 */

import { dashboardApiBase } from "./api-base"
import { dashboardApiUrl, dashboardForwardedHeaders } from "./dashboard-fetch"

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string
  ) {
    super(message)
    this.name = "ApiError"
  }
}

export function isGithubReauthError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false
  if (error.status === 401) return true
  return /github token|re-login required/i.test(error.message)
}

async function apiError(res: Response): Promise<ApiError> {
  let message = res.statusText
  try {
    const body = await res.json()
    if (body?.detail) {
      message =
        typeof body.detail === "string"
          ? body.detail
          : JSON.stringify(body.detail)
    }
  } catch {
    /* keep the status text */
  }
  return new ApiError(res.status, message)
}

export async function request<T>(
  path: string,
  init: RequestInit = {}
): Promise<T> {
  const res = await fetch(dashboardApiUrl(path), {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...dashboardForwardedHeaders(),
      ...(init.headers ?? {}),
    },
  })
  if (!res.ok) throw await apiError(res)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export interface DownloadedFile {
  blob: Blob
  filename: string
}

function filenameFromContentDisposition(value: string | null): string | null {
  const match = /filename="([^"]+)"/.exec(value ?? "")
  return match?.[1] ?? null
}

export async function requestFile(
  path: string,
  options: { accept: string; fallbackFilename: string }
): Promise<DownloadedFile> {
  const res = await fetch(dashboardApiUrl(path), {
    credentials: "include",
    headers: { Accept: options.accept, ...dashboardForwardedHeaders() },
  })
  if (!res.ok) throw await apiError(res)
  return {
    blob: await res.blob(),
    filename:
      filenameFromContentDisposition(res.headers.get("content-disposition")) ??
      options.fallbackFilename,
  }
}

/**
 * Browser-visible URL for a dashboard endpoint, for the cases a `fetch` can't
 * serve: OAuth redirects, `<img>`/stream sources, and the LangGraph SDK's own
 * client (which builds `new URL(apiUrl + path)` and so needs an absolute base).
 */
export function dashboardApiHref(path: string): string {
  return `${dashboardApiBase()}/dashboard/api${path}`
}
