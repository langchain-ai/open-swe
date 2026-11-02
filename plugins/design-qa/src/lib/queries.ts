import {
  HostError,
  type FrontendReviewInbox,
  type FrontendReviewRun,
  type FrontendReviews,
  type OpenPullRequest,
} from "@openswe/plugin-sdk"
import { usePluginHost } from "@openswe/plugin-sdk/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"

import { repositoryOf, type PullRequestRef } from "./pullRequest"

export type InboxScope = "review-requested" | "mine"

/** One run's media, which `frontendReviews(repository).media()` reads by name. */
export interface RunMedia {
  repository: string
  pull: number
  run: string
}

export const keys = {
  repositories: ["repositories"] as const,
  inbox: (scope: InboxScope) => ["inbox", scope] as const,
  runs: (pr: PullRequestRef) => ["runs", pr.owner, pr.repo, pr.number] as const,
  run: (pr: PullRequestRef, id: string) =>
    ["run", pr.owner, pr.repo, pr.number, id] as const,
  media: (target: RunMedia, name: string) =>
    ["media", target.repository, target.pull, target.run, name] as const,
}

export const isForbidden = (error: unknown) =>
  error instanceof HostError && error.status === 403

export const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : String(error)

/** Retries once, but not a request the web UI or backend refused: it would be refused again. */
export const retryOnce = (failures: number, error: Error) =>
  failures < 1 &&
  !(error instanceof HostError && error.status !== null && error.status < 500)

/** The repositories the person shared, the only ones Design QA can read. */
export function useRepositories() {
  const host = usePluginHost()
  return useQuery({
    queryKey: keys.repositories,
    queryFn: () => host.repositories.list(),
    staleTime: Infinity,
  })
}

/**
 * Asks the person to share repositories, suggesting `repository` when given,
 * then reloads what failed without them.
 */
export function useShareRepositories() {
  const host = usePluginHost()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (repository?: string) =>
      host.repositories.pick({
        multiple: true,
        suggested: repository ? [repository] : undefined,
      }),
    onSuccess: async (picked) => {
      if (picked.length === 0) return
      await queryClient.invalidateQueries({
        predicate: (query) =>
          query.state.status === "error" ||
          query.queryKey[0] === "repositories" ||
          query.queryKey[0] === "inbox",
      })
    },
  })
}

export function useInbox(scope: InboxScope) {
  const host = usePluginHost()
  return useQuery({
    queryKey: keys.inbox(scope),
    queryFn: () => host.frontendReviewInbox(scope),
    staleTime: 60_000,
  })
}

/** What the inbox already loaded about `pr`, such as its title and current head. */
export function useListedPullRequest(
  pr: PullRequestRef
): OpenPullRequest | undefined {
  const queryClient = useQueryClient()
  const repository = repositoryOf(pr).toLowerCase()
  for (const scope of ["review-requested", "mine"] as const) {
    const item = queryClient
      .getQueryData<FrontendReviewInbox>(keys.inbox(scope))
      ?.items.find(
        (listed) =>
          listed.pr.repo.toLowerCase() === repository &&
          listed.pr.number === pr.number
      )
    if (item) return item.pr
  }
  return undefined
}

export function useRuns(pr: PullRequestRef) {
  const host = usePluginHost()
  return useQuery({
    queryKey: keys.runs(pr),
    queryFn: () => host.frontendReviews(repositoryOf(pr)).list(pr.number),
  })
}

const inProgress = (run: FrontendReviewRun | undefined) =>
  run?.status === "pending" ||
  run?.status === "running" ||
  run?.report?.ai_review_status === "running"

export function useRun(pr: PullRequestRef, id: string | undefined) {
  const host = usePluginHost()
  return useQuery({
    queryKey: keys.run(pr, id ?? ""),
    queryFn: async () => {
      const run = await host
        .frontendReviews(repositoryOf(pr))
        .get(pr.number, id ?? "")
      if (!run) throw new Error("this run is no longer saved")
      return run
    },
    enabled: Boolean(id),
    // No event reaches the frame as a capture or its assessment moves on.
    refetchInterval: (query) => (inProgress(query.state.data) ? 15_000 : false),
  })
}

export async function readMedia(
  reviews: FrontendReviews,
  target: RunMedia,
  name: string
): Promise<Blob> {
  const blob = await reviews.media(target.pull, target.run, name)
  if (!blob) throw new Error("This capture no longer has that file.")
  return blob
}

/** A run's media file as a Blob, read when `name` is set and `enabled`. */
export function useMedia(
  target: RunMedia,
  name: string | null,
  enabled = true
) {
  const host = usePluginHost()
  return useQuery({
    queryKey: keys.media(target, name ?? ""),
    queryFn: () =>
      readMedia(host.frontendReviews(target.repository), target, name ?? ""),
    enabled: enabled && name !== null,
    staleTime: Infinity,
    gcTime: 60_000,
  })
}

/** Names the web UI's tab after what is open; `null` leaves it named Design QA. */
export function useTitle(title: string | null) {
  const host = usePluginHost()
  useEffect(() => {
    void host.ui.setTitle(title)
  }, [host, title])
}
