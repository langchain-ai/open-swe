import { useEffect } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { ApiError, api } from "./api"
import {
  REPOS_CACHE_MAX_AGE_MS,
  readCachedRepos,
  writeCachedRepos,
} from "./repoCache"
import { useSession } from "./session"
import type { Profile, ProfileUpdate, ReposPayload } from "./api"

export function useProfile() {
  const session = useSession()
  return useQuery({
    queryKey: ["profile"],
    queryFn: api.profile,
    enabled: !!session.data,
  })
}

export function useOptions() {
  const session = useSession()
  return useQuery({
    queryKey: ["options"],
    queryFn: api.options,
    enabled: !!session.data,
  })
}

export const reposQueryKey = ["repos"] as const

/** Repos change rarely; revalidate in the background rather than on every mount. */
export const REPOS_STALE_TIME_MS = 10 * 60 * 1000

/**
 * Accessible repos, seeded from localStorage so the picker populates instantly
 * instead of waiting on the multi-second GitHub installation fan-out.
 */
export function useRepos() {
  const session = useSession()
  const login = session.data?.login ?? null
  const qc = useQueryClient()

  useEffect(() => {
    if (!login) return
    if (qc.getQueryData<ReposPayload>(reposQueryKey)) return
    const cached = readCachedRepos(login)
    if (!cached) return
    qc.setQueryData<ReposPayload>(reposQueryKey, cached.payload, {
      updatedAt: cached.updatedAt,
    })
  }, [login, qc])

  return useQuery({
    queryKey: reposQueryKey,
    queryFn: async () => {
      try {
        const payload = await api.repos()
        if (login) writeCachedRepos(login, payload)
        return payload
      } catch (e) {
        if (e instanceof ApiError && e.status === 401)
          return { installations: [], repositories: [] }
        throw e
      }
    },
    enabled: !!session.data,
    staleTime: REPOS_STALE_TIME_MS,
    gcTime: REPOS_CACHE_MAX_AGE_MS,
  })
}

/** Force a fresh GitHub fan-out, e.g. after installing the App on new repos. */
export function useRefreshRepos() {
  const session = useSession()
  const login = session.data?.login ?? null
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.repos({ refresh: true }),
    onSuccess: (payload) => {
      qc.setQueryData<ReposPayload>(reposQueryKey, payload)
      if (login) writeCachedRepos(login, payload)
    },
  })
}

export function useSaveProfile() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: ProfileUpdate) => api.saveProfile(body),
    onSuccess: (saved) => {
      qc.setQueryData(["profile"], saved)
    },
  })
}

/**
 * Build a ProfileUpdate body from the current cached profile plus a patch,
 * so individual pages can mutate one field without losing values managed by
 * other pages.
 */
export function buildProfileUpdate(
  current: Profile | undefined,
  patch: Partial<ProfileUpdate>,
  fallbackModel: string,
  fallbackEffort: string
): ProfileUpdate {
  return {
    default_model: current?.default_model ?? fallbackModel,
    reasoning_effort: current?.reasoning_effort ?? fallbackEffort,
    default_subagent_model:
      current?.default_subagent_model ?? current?.default_model ?? fallbackModel,
    subagent_reasoning_effort:
      current?.subagent_reasoning_effort ?? current?.reasoning_effort ?? fallbackEffort,
    default_repo: current?.default_repo ?? null,
    base_branch: current?.base_branch ?? null,
    branch_prefix: current?.branch_prefix ?? null,
    auto_fix_ci: current?.auto_fix_ci ?? true,
    create_prs: current?.create_prs ?? false,
    review_draft_prs: current?.review_draft_prs ?? null,
    ...patch,
  }
}
