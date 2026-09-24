import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { ApiError, api, DEFAULT_WORKSPACE_SLUG } from "./api"
import {
  REPOS_CACHE_MAX_AGE_MS,
  readCachedRepos,
  writeCachedRepos,
} from "./repoCache"
import { useSession } from "./session"
import type { Profile, ProfileUpdate, ReposPayload } from "./api"

const profileQueryKey = (login: string | undefined) => [
  "profile",
  login ?? null,
]

export function useProfile() {
  const session = useSession()
  return useQuery({
    queryKey: profileQueryKey(session.data?.login),
    queryFn: api.profile,
    enabled: !!session.data,
  })
}

/**
 * Selectable models and defaults for the workspace a run will land in; model
 * defaults and the Fable flag are per workspace, so the key carries the slug.
 */
export function useOptions(workspace?: string | null) {
  const slug = workspace ?? DEFAULT_WORKSPACE_SLUG
  return useQuery({
    queryKey: ["options", slug],
    queryFn: () => api.options(slug),
  })
}

/** Keyed by login so a cached list never bleeds across accounts in one SPA session. */
export const reposQueryKey = (login: string | null) => ["repos", login] as const

/** Repos change rarely; revalidate in the background rather than on every mount. */
export const REPOS_STALE_TIME_MS = 10 * 60 * 1000

/**
 * Accessible repos, seeded from localStorage so the picker populates instantly
 * instead of waiting on the multi-second GitHub installation fan-out.
 */
export function useRepos() {
  const session = useSession()
  const login = session.data?.login ?? null
  const cached = login ? readCachedRepos(login) : null

  return useQuery({
    queryKey: reposQueryKey(login),
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
    initialData: cached?.payload,
    initialDataUpdatedAt: cached?.updatedAt,
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
      qc.setQueryData<ReposPayload>(reposQueryKey(login), payload)
      if (login) writeCachedRepos(login, payload)
    },
  })
}

export function useSaveProfile() {
  const qc = useQueryClient()
  const session = useSession()
  const key = profileQueryKey(session.data?.login)
  return useMutation({
    mutationFn: (body: ProfileUpdate) => api.saveProfile(body),
    onSuccess: (saved) => {
      qc.setQueryData(key, saved)
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
    default_subagent_model: current?.default_subagent_model ?? null,
    subagent_reasoning_effort:
      current?.default_subagent_model == null
        ? null
        : (current.subagent_reasoning_effort ?? fallbackEffort),
    default_repo: current?.default_repo ?? null,
    base_branch: current?.base_branch ?? null,
    branch_prefix: current?.branch_prefix ?? null,
    auto_fix_ci: current?.auto_fix_ci ?? true,
    model_routing_enabled: current?.model_routing_enabled ?? null,
    recent_thread_context_enabled:
      current?.recent_thread_context_enabled ?? false,
    draft_prs: current?.draft_prs ?? true,
    review_draft_prs: current?.review_draft_prs ?? null,
    experimental_assistant_ui: current?.experimental_assistant_ui ?? null,
    ...patch,
  }
}

export function useExperimentalAssistantUi(): boolean {
  const profile = useProfile()
  return (
    profile.data?.experimental_assistant_ui ??
    import.meta.env.VITE_EXPERIMENTAL_ASSISTANT_UI === "true"
  )
}
