/**
 * localStorage cache for the repo picker payload.
 *
 * `/repos` fans out over every GitHub App installation, so a cold call takes
 * 10s+ for users with hundreds of accessible repos. Seeding the query cache
 * from here lets the picker paint immediately on load and revalidate in the
 * background. Entries are keyed by login so one account never sees another's
 * repos after a user switch.
 */

import type { Installation, ReposPayload, Repository } from "./api"

const STORAGE_KEY = "open-swe.repos.cache.v1"

export const REPOS_CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1000

export interface CachedRepos {
  login: string
  updatedAt: number
  payload: ReposPayload
}

function sanitizeRepositories(value: unknown): Array<Repository> {
  if (!Array.isArray(value)) return []
  const out: Array<Repository> = []
  for (const entry of value) {
    if (!entry || typeof entry !== "object") continue
    const raw = entry as Record<string, unknown>
    if (typeof raw.full_name !== "string" || !raw.full_name) continue
    out.push({ full_name: raw.full_name, private: raw.private === true })
  }
  return out
}

function sanitizeInstallations(value: unknown): Array<Installation> {
  if (!Array.isArray(value)) return []
  const out: Array<Installation> = []
  for (const entry of value) {
    if (!entry || typeof entry !== "object") continue
    const raw = entry as Record<string, unknown>
    if (typeof raw.id !== "number") continue
    out.push({
      id: raw.id,
      account: typeof raw.account === "string" ? raw.account : null,
      account_type:
        typeof raw.account_type === "string" ? raw.account_type : null,
    })
  }
  return out
}

export function readCachedRepos(login: string): CachedRepos | null {
  if (typeof window === "undefined" || !login) return null
  let raw: string | null
  try {
    raw = window.localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
  if (!raw) return null
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (!parsed || typeof parsed !== "object") return null
  const record = parsed as Record<string, unknown>
  if (typeof record.login !== "string" || record.login !== login) return null
  if (typeof record.updatedAt !== "number") return null
  if (Date.now() - record.updatedAt > REPOS_CACHE_MAX_AGE_MS) return null
  const payload =
    record.payload && typeof record.payload === "object"
      ? (record.payload as Record<string, unknown>)
      : null
  if (!payload) return null
  const repositories = sanitizeRepositories(payload.repositories)
  if (!repositories.length) return null
  return {
    login,
    updatedAt: record.updatedAt,
    payload: {
      installations: sanitizeInstallations(payload.installations),
      repositories,
    },
  }
}

export function writeCachedRepos(login: string, payload: ReposPayload): void {
  if (typeof window === "undefined" || !login) return
  try {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ login, updatedAt: Date.now(), payload })
    )
  } catch {
    /* ignore persistence failures (private mode, quota, SSR) */
  }
}

export function clearCachedRepos(): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    /* ignore */
  }
}
