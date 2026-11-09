import {
  HostError,
  type PluginHost,
  type PullRequest,
} from "@openswe/plugin-sdk"
import { usePluginHost } from "@openswe/plugin-sdk/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"

import {
  CAPTURES,
  captureKey,
  capturePrefix,
  type CaptureKey,
  type FrontendReviewRun,
} from "./capture"
import { repositoryOf, type PullRequestRef } from "./pullRequest"

export type InboxScope = "review-requested" | "mine"

/** One run's media: attachments of its capture document. */
export interface RunMedia {
  repository: string
  pull: number
  run: string
}

/** A pull request's latest captures, newest first, and the pull request as GitHub has it now. */
export interface PullRequestRuns {
  runs: FrontendReviewRun[]
  /** `null` when GitHub would not give it. */
  pull: PullRequest | null
}

/** An open pull request with a capture, in a repository the person shared. */
export interface InboxItem {
  repository: string
  pull: PullRequest
  /** Its latest capture. */
  run: FrontendReviewRun
}

export interface Inbox {
  items: InboxItem[]
  /** Whether some pull requests may be missing: GitHub left some out, or would not give one. */
  incomplete: boolean
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

/** Design QA's reads, through the host: its captures in storage and pull requests on GitHub. */
export class DesignQa {
  private static readonly byHost = new WeakMap<PluginHost, DesignQa>()

  private constructor(private readonly host: PluginHost) {}

  static of(host: PluginHost): DesignQa {
    let found = DesignQa.byHost.get(host)
    if (!found) {
      found = new DesignQa(host)
      DesignQa.byHost.set(host, found)
    }
    return found
  }

  captures(repository: string) {
    return this.host.storage.collection<CaptureKey, FrontendReviewRun>(
      CAPTURES,
      { repository }
    )
  }

  async latestRuns(
    repository: string,
    pull: number,
    limit: number
  ): Promise<FrontendReviewRun[]> {
    const found = await this.captures(repository).query({
      prefix: capturePrefix(pull),
      direction: "desc",
      limit,
    })
    return found.map(({ document }) => document)
  }

  /** The pull request as GitHub has it now; `undefined` when GitHub would not answer. */
  currentPull(repository: string, pull: number) {
    return this.host
      .github(repository)
      .pull(pull)
      .catch((error: unknown) => {
        console.warn("Could not read a pull request from GitHub", error)
        return undefined
      })
  }

  /**
   * Open pull requests asking the person (or a team of theirs) for review, or
   * their own, that have a capture, with each one's latest.
   */
  async inbox(scope: InboxScope): Promise<Inbox> {
    const search = await this.host.pulls.search({
      role: scope === "mine" ? "author" : "review-requested",
    })
    const rows = await Promise.all(
      search.pulls.map(async ({ repository, number }) => {
        const [run] = await this.latestRuns(repository, number, 1)
        if (!run) return null
        return {
          repository,
          run,
          pull: await this.currentPull(repository, number),
        }
      })
    )
    const captured = rows.filter((row) => row !== null)
    return {
      items: captured.flatMap(({ repository, run, pull }) =>
        pull?.state === "open" ? [{ repository, run, pull }] : []
      ),
      incomplete:
        search.incomplete || captured.some(({ pull }) => pull === undefined),
    }
  }

  async runs(pr: PullRequestRef): Promise<PullRequestRuns> {
    const repository = repositoryOf(pr)
    const [runs, pull] = await Promise.all([
      this.latestRuns(repository, pr.number, 20),
      this.currentPull(repository, pr.number),
    ])
    return { runs, pull: pull ?? null }
  }

  async run(pr: PullRequestRef, id: string): Promise<FrontendReviewRun> {
    const found = await this.captures(repositoryOf(pr)).get(
      captureKey(pr.number, id)
    )
    if (!found) throw new Error("this run is no longer saved")
    return found.document
  }

  async media(target: RunMedia, name: string): Promise<Blob> {
    const blob = await this.captures(target.repository).attachment(
      captureKey(target.pull, target.run),
      name
    )
    if (!blob) throw new Error("This capture no longer has that file.")
    return blob
  }
}

export const useDesignQa = () => DesignQa.of(usePluginHost())

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
  const qa = useDesignQa()
  return useQuery({
    queryKey: keys.inbox(scope),
    queryFn: () => qa.inbox(scope),
    staleTime: 60_000,
  })
}

/** The pull request's latest captures, and its title and current head as GitHub has them now. */
export function useRuns(pr: PullRequestRef) {
  const qa = useDesignQa()
  return useQuery({
    queryKey: keys.runs(pr),
    queryFn: () => qa.runs(pr),
  })
}

export function useRun(pr: PullRequestRef, id: string | undefined) {
  const qa = useDesignQa()
  return useQuery({
    queryKey: keys.run(pr, id ?? ""),
    queryFn: () => qa.run(pr, id ?? ""),
    enabled: Boolean(id),
  })
}

/**
 * Reloads the pull request's captures and their files whenever anyone writes
 * one, or its files. The watch does not say which file changed, so every file
 * of the pull request is read again: a file can be replaced under its name.
 */
export function useCaptureChanges(pr: PullRequestRef) {
  const qa = useDesignQa()
  const queryClient = useQueryClient()
  const repository = repositoryOf(pr)
  const { owner, repo, number } = pr
  useEffect(
    () =>
      qa.captures(repository).watch(() => {
        const ref = { owner, repo, number }
        void queryClient.invalidateQueries({ queryKey: keys.runs(ref) })
        void queryClient.invalidateQueries({
          queryKey: ["run", owner, repo, number],
        })
        void queryClient.invalidateQueries({
          queryKey: ["media", repository, number],
        })
      }),
    [qa, queryClient, repository, owner, repo, number]
  )
}

/** A run's media file as a Blob, read when `name` is set and `enabled`. */
export function useMedia(
  target: RunMedia,
  name: string | null,
  enabled = true
) {
  const qa = useDesignQa()
  return useQuery({
    queryKey: keys.media(target, name ?? ""),
    queryFn: () => qa.media(target, name ?? ""),
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
