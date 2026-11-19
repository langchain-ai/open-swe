import {
  HostError,
  type Collection,
  type JsonObject,
  type PluginHost,
  type PullRequest,
  type PullRequestSearch,
  type PullRequestSummary,
  type ReviewOutcome,
  type StoredDocument,
} from "@openswe/plugin-sdk"
import { usePluginHost } from "@openswe/plugin-sdk/react"
import {
  keepPreviousData,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { useEffect } from "react"

import {
  CAPTURES,
  captureKey,
  capturePrefix,
  type CaptureKey,
  type FrontendReviewRun,
  type FrontendScenario,
  StoredCapture,
} from "./capture"
import { PixelChange } from "./pixelChange"
import { repositoryOf, type PullRequestRef } from "./pullRequest"
import { InboxOrder, type InboxPreferences } from "./inboxOrder"
import type { InboxScope } from "./route"

/** The most documents one storage query returns. */
const CAPTURE_PAGE = 500

/** How long the inbox waits after a capture changes before reading again, so one save's writes read once. */
const INBOX_SETTLE_MS = 3_000

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
  outcome: ReviewOutcome | null
}

/** An open pull request and its latest capture attempt, including those without evidence. */
export interface InboxItem {
  repository: string
  pull: PullRequest | PullRequestSummary
  /** Its latest capture. */
  run: FrontendReviewRun | null
  outcome: ReviewOutcome | null
}

/** One page of the inbox. */
export interface Inbox {
  items: InboxItem[]
  /** Whether some pull requests may be missing: GitHub would not give one. */
  incomplete: boolean
}

/** A repository's latest capture and capture outcome of each pull request, by number. */
export interface RepositoryCaptures {
  runs: Map<number, FrontendReviewRun>
  outcomes: Map<number, ReviewOutcome>
}

export const keys = {
  repositories: ["repositories"] as const,
  inboxSearch: (scope: InboxScope) => ["inbox", "search", scope] as const,
  inboxPages: ["inbox", "page"] as const,
  inboxPage: (scope: InboxScope, pulls: readonly PullRequestSummary[]) =>
    [
      "inbox",
      "page",
      scope,
      // A pull request GitHub updated, such as a new title or head, reads again.
      ...pulls.map(
        ({ repository, number, updatedAt }) =>
          `${repository}#${number}@${updatedAt ?? ""}`
      ),
    ] as const,
  repositoryCaptures: (repository: string) =>
    ["inbox", "captures", repository] as const,
  preferences: ["preferences"] as const,
  runs: (pr: PullRequestRef) => ["runs", pr.owner, pr.repo, pr.number] as const,
  run: (pr: PullRequestRef, id: string) =>
    ["run", pr.owner, pr.repo, pr.number, id] as const,
  media: (target: RunMedia, name: string) =>
    ["media", target.repository, target.pull, target.run, name] as const,
  change: (target: RunMedia, before: string, after: string) =>
    [
      "media",
      target.repository,
      target.pull,
      target.run,
      before,
      after,
    ] as const,
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
    return this.host.storage.collection<CaptureKey, JsonObject>(CAPTURES, {
      repository,
    })
  }

  outcomes(repository: string) {
    return this.host.storage.collection<CaptureKey, JsonObject>(
      "review_outcomes",
      { repository }
    )
  }

  static outcome(
    stored: StoredDocument<JsonObject, CaptureKey>
  ): ReviewOutcome | null {
    const value = stored.document
    const status = value.status
    if (
      status !== "queued" &&
      status !== "complete" &&
      status !== "blocked" &&
      status !== "failed" &&
      status !== "skipped"
    )
      return null
    if (
      typeof value.id !== "string" ||
      typeof value.head_sha !== "string" ||
      typeof value.created_at !== "string"
    )
      return null
    return {
      id: value.id,
      head_sha: value.head_sha,
      status,
      created_at: value.created_at,
      reason: typeof value.reason === "string" ? value.reason : "",
    }
  }

  private async *documents(
    collection: Collection<CaptureKey, JsonObject>,
    prefix = "pulls/"
  ) {
    let after: CaptureKey | undefined
    for (;;) {
      const page = await collection.query({
        prefix,
        startAfter: after,
        limit: CAPTURE_PAGE,
      })
      yield* page
      if (page.length < CAPTURE_PAGE) return
      after = page[page.length - 1]?.key
    }
  }

  async latestOutcomes(
    repository: string,
    pull?: number
  ): Promise<Map<number, ReviewOutcome>> {
    const latest = new Map<number, ReviewOutcome>()
    const prefix = pull ? capturePrefix(pull) : "pulls/"
    for await (const stored of this.documents(
      this.outcomes(repository),
      prefix
    )) {
      const number = Number(stored.key.split("/")[1])
      const outcome = DesignQa.outcome(stored)
      const previous = latest.get(number)
      if (outcome && (!previous || outcome.created_at >= previous.created_at))
        latest.set(number, outcome)
    }
    return latest
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
    return found.map((stored) =>
      StoredCapture.settled(StoredCapture.read(stored))
    )
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
   * Each pull request's latest capture in `repository`, by number. One read
   * of the whole collection, in pages, costs far less than one per pull
   * request: closing a pull request deletes its captures, so few are kept.
   */
  async latestRunByPull(
    repository: string
  ): Promise<Map<number, FrontendReviewRun>> {
    const latest = new Map<number, StoredDocument<JsonObject, CaptureKey>>()
    for await (const stored of this.documents(this.captures(repository))) {
      const pull = Number(stored.key.split("/")[1])
      const kept = latest.get(pull)
      // Ids are time-ordered, so the greatest key is the newest capture.
      if (!kept || stored.key > kept.key) latest.set(pull, stored)
    }
    return new Map(
      [...latest].map(([pull, stored]) => [
        pull,
        StoredCapture.settled(StoredCapture.read(stored)),
      ])
    )
  }

  /**
   * Open pull requests asking the person (or a team of theirs) for review, or
   * their own, as GitHub's search lists them: no captures read yet.
   */
  search(scope: InboxScope): Promise<PullRequestSearch> {
    return this.host.pulls.search({
      role: scope === "mine" ? "author" : "review-requested",
    })
  }

  /** The person's own inbox preferences, kept private to them. */
  private preferenceStore() {
    return this.host.storage.collection<"inbox", JsonObject>("preferences")
  }

  async preferences(): Promise<InboxPreferences> {
    return InboxOrder.read(
      (await this.preferenceStore().get("inbox"))?.document
    )
  }

  async savePreferences(preferences: InboxPreferences): Promise<void> {
    await this.preferenceStore().put("inbox", {
      pageSize: preferences.pageSize,
      sort: preferences.sort,
    })
  }

  /**
   * Whether the review skipped Design QA's work on pull request `number` and
   * nothing was ever captured for it, as for a change with no frontend files.
   */
  static skipped(found: RepositoryCaptures | undefined, number: number) {
    return (
      found?.outcomes.get(number)?.status === "skipped" &&
      !found.runs.has(number)
    )
  }

  async repositoryCaptures(repository: string): Promise<RepositoryCaptures> {
    const [runs, outcomes] = await Promise.all([
      this.latestRunByPull(repository),
      this.latestOutcomes(repository),
    ])
    return { runs, outcomes }
  }

  /**
   * One page of searched pull requests with their latest captures, including
   * those without one; `captures` reads each repository's, so a caller can
   * keep them between pages.
   */
  async inbox(
    pulls: readonly PullRequestSummary[],
    captures: (repository: string) => Promise<RepositoryCaptures> = (
      repository
    ) => this.repositoryCaptures(repository)
  ): Promise<Inbox> {
    const repositories = [...new Set(pulls.map(({ repository }) => repository))]
    const read = new Map(
      await Promise.all(
        repositories.map(
          async (repository) =>
            [repository, await captures(repository)] as const
        )
      )
    )
    let incomplete = false
    const items = await Promise.all(
      pulls.map(async (summary) => {
        const { repository, number } = summary
        const found = read.get(repository)
        const run = found?.runs.get(number) ?? null
        // Search already supplies the uncaptured PR's identity and title.
        const current = run
          ? await this.currentPull(repository, number)
          : undefined
        if (run && current === undefined) incomplete = true
        if (current && current.state !== "open") return null
        return {
          repository,
          pull: current ?? summary,
          run,
          outcome: found?.outcomes.get(number) ?? null,
        }
      })
    )
    return { items: items.filter((item) => item !== null), incomplete }
  }

  async runs(pr: PullRequestRef): Promise<PullRequestRuns> {
    const repository = repositoryOf(pr)
    const [runs, pull, outcomes] = await Promise.all([
      this.latestRuns(repository, pr.number, 20),
      this.currentPull(repository, pr.number),
      this.latestOutcomes(repository, pr.number),
    ])
    return {
      runs,
      pull: pull ?? null,
      outcome: outcomes.get(pr.number) ?? null,
    }
  }

  async run(pr: PullRequestRef, id: string): Promise<FrontendReviewRun> {
    const found = await this.captures(repositoryOf(pr)).get(
      captureKey(pr.number, id)
    )
    if (!found) throw new Error("this run is no longer saved")
    return StoredCapture.settled(StoredCapture.read(found))
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

export function useInboxSearch(scope: InboxScope) {
  const qa = useDesignQa()
  return useQuery({
    queryKey: keys.inboxSearch(scope),
    queryFn: () => qa.search(scope),
    staleTime: 60_000,
    // Review requests come and go on GitHub, which tells the app nothing.
    refetchInterval: 5 * 60_000,
    refetchOnWindowFocus: true,
  })
}

/**
 * Each repository's latest captures and outcomes, by repository, in the cache
 * an inbox page reads from; `pending` until every one has loaded.
 */
export function useRepositoryCaptures(repositories: readonly string[]) {
  const qa = useDesignQa()
  return useQueries({
    queries: repositories.map((repository) => ({
      queryKey: keys.repositoryCaptures(repository),
      queryFn: () => qa.repositoryCaptures(repository),
      staleTime: 60_000,
    })),
    combine: (results) => ({
      byRepository: new Map(
        results.flatMap((result, index) =>
          result.data ? [[repositories[index] ?? "", result.data] as const] : []
        )
      ),
      pending: results.some((result) => result.isPending),
      error: results.find((result) => result.error)?.error ?? null,
    }),
  })
}

/** How many pull requests a page shows and in what order, saved for the person as they change it. */
export function useInboxPreferences() {
  const host = usePluginHost()
  const qa = DesignQa.of(host)
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: keys.preferences,
    queryFn: () => qa.preferences(),
    staleTime: Infinity,
  })
  const save = useMutation({
    mutationFn: (next: InboxPreferences) => qa.savePreferences(next),
    onMutate: (next) => {
      const previous = queryClient.getQueryData<InboxPreferences>(
        keys.preferences
      )
      queryClient.setQueryData(keys.preferences, next)
      return { previous }
    },
    onError: (error, _next, context) => {
      queryClient.setQueryData(keys.preferences, context?.previous)
      void host.ui.toast({
        title: "Could not save your inbox settings",
        description: errorMessage(error),
        intent: "error",
      })
    },
  })
  const preferences = query.data ?? InboxOrder.defaults
  return {
    preferences,
    change: (update: Partial<InboxPreferences>) =>
      save.mutate({ ...preferences, ...update }),
  }
}

/** The captures of one inbox page's pull requests, keeping each repository's read between pages. */
export function useInboxPage(
  scope: InboxScope,
  pulls: readonly PullRequestSummary[]
) {
  const qa = useDesignQa()
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: keys.inboxPage(scope, pulls),
    queryFn: () =>
      qa.inbox(pulls, (repository) =>
        queryClient.fetchQuery({
          queryKey: keys.repositoryCaptures(repository),
          queryFn: () => qa.repositoryCaptures(repository),
          staleTime: 60_000,
        })
      ),
    enabled: pulls.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    // As the search does: a head can move without GitHub's search noticing yet.
    refetchInterval: 5 * 60_000,
    refetchOnWindowFocus: true,
  })
}

/**
 * Reads the inbox's captures again, once things settle, whenever a review
 * saves or changes a capture in one of `repositories`; GitHub's search stays.
 */
export function useInboxChanges(repositories: readonly string[]) {
  const qa = useDesignQa()
  const queryClient = useQueryClient()
  const watched = repositories.join("\n")
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined
    const changed = () => {
      clearTimeout(timer)
      timer = setTimeout(() => {
        void queryClient.invalidateQueries({
          queryKey: ["inbox", "captures"],
        })
        void queryClient.invalidateQueries({ queryKey: keys.inboxPages })
      }, INBOX_SETTLE_MS)
    }
    const stops = watched
      .split("\n")
      .filter(Boolean)
      .flatMap((repository) => [
        qa.captures(repository).watch(changed),
        qa.outcomes(repository).watch(changed),
      ])
    return () => {
      clearTimeout(timer)
      for (const stop of stops) stop()
    }
  }, [qa, queryClient, watched])
}

/** The pull request's latest captures, and its title and current head as GitHub has them now. */
export function useRuns(pr: PullRequestRef) {
  const qa = useDesignQa()
  return useQuery({
    queryKey: keys.runs(pr),
    queryFn: () => qa.runs(pr),
    refetchInterval: 60_000,
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
  useEffect(() => {
    const changed = () => {
      const ref = { owner, repo, number }
      void queryClient.invalidateQueries({ queryKey: keys.runs(ref) })
      void queryClient.invalidateQueries({
        queryKey: ["run", owner, repo, number],
      })
      void queryClient.invalidateQueries({
        queryKey: ["media", repository, number],
      })
    }
    const stops = [
      qa.captures(repository).watch(changed),
      qa.outcomes(repository).watch(changed),
    ]
    return () => {
      for (const stop of stops) stop()
    }
  }, [qa, queryClient, repository, owner, repo, number])
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

/**
 * How much of a scenario's screenshots changed, compared in the page once
 * `enabled`; nothing for a scenario missing one, and null for a pair that
 * can't be compared.
 */
export function usePixelChange(
  target: RunMedia,
  scenario: FrontendScenario,
  enabled = true
) {
  const qa = useDesignQa()
  const queryClient = useQueryClient()
  const { before_url: before, after_url: after } = scenario
  return useQuery({
    queryKey: keys.change(target, before ?? "", after ?? ""),
    queryFn: async () => {
      if (!before || !after) return null
      // The same cache the screenshots show from, so each downloads once.
      const [first, second] = await Promise.all(
        [before, after].map((name) =>
          queryClient.fetchQuery({
            queryKey: keys.media(target, name),
            queryFn: () => qa.media(target, name),
            staleTime: Infinity,
          })
        )
      )
      return first && second ? PixelChange.between(first, second) : null
    },
    enabled: enabled && before !== null && after !== null,
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
