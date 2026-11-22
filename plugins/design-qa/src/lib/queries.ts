import {
  HostError,
  type Collection,
  type JsonObject,
  type Person,
  type PluginHost,
  type PullRequest,
  type PullRequestSearch,
  type PullRequestSummary,
  type ReviewOutcome,
  type StoredDocument,
} from "@openswe/plugin-sdk"
import { usePluginHost } from "@openswe/plugin-sdk/react"
import {
  infiniteQueryOptions,
  keepPreviousData,
  queryOptions,
  useInfiniteQuery,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query"
import { useEffect } from "react"

import {
  CAPTURES,
  CaptureAttempt,
  captureKey,
  capturePrefix,
  type CaptureKey,
  type FrontendReviewRun,
  type FrontendScenario,
  type ScenarioShots,
  StoredCapture,
} from "./capture"
import { InboxSnapshot } from "./inboxSnapshot"
import { Limiter } from "./limiter"
import { Pins, type Pin, type PinKey } from "./pins"
import { PixelChange } from "./pixelChange"
import { repositoryOf, type PullRequestRef } from "./pullRequest"
import { InboxOrder, type InboxPreferences } from "./inboxOrder"
import type { InboxScope } from "./route"

/** An inbox tab GitHub's search fills, rather than the person's pins. */
export type SearchScope = Exclude<InboxScope, "pinned" | "people">

/** The most pull requests read from GitHub at once, leaving the web UI's request budget room for the rest. */
const PULL_READS = 6

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

/** An inbox tab's pull requests, as far as they have loaded. */
export interface InboxSearch {
  /** `undefined` until the first page, or the last visit's snapshot, is read. */
  pulls: PullRequestSummary[] | undefined
  /** Whether `pulls` is the last visit's list, shown while GitHub's search runs again. */
  remembered: boolean
  /** Whether some pull requests may be missing: GitHub's search timed out or a page failed. */
  incomplete: boolean
  fetching: boolean
  /** Whether later pages of GitHub's search are still loading. */
  more: boolean
  error: Error | null
}

/** One page's cards, each as GitHub has it now once read. */
export interface InboxPage {
  items: InboxItem[]
  /** Whether GitHub would not give one of them. */
  incomplete: boolean
  fetching: boolean
}

/** A repository's latest capture and capture outcome of each pull request, by number. */
export interface RepositoryCaptures {
  runs: Map<number, FrontendReviewRun>
  outcomes: Map<number, ReviewOutcome>
}

export const keys = {
  repositories: ["repositories"] as const,
  /** A tab's search, or, with `author`, the People tab's search of that login's pull requests. */
  inboxSearch: (scope: SearchScope, author?: string) =>
    ["inbox", "search", scope, author?.toLowerCase() ?? ""] as const,
  /** A pull request GitHub updated, such as with a new title or head, reads again. */
  inboxPull: ({ repository, number, updatedAt }: PullRequestSummary) =>
    ["inbox", "pull", repository, number, updatedAt ?? ""] as const,
  /** A pin's pull request, read whatever it was when pinned. */
  pinnedPull: (repository: string, number: number) =>
    ["inbox", "pin", repository, number] as const,
  inboxSnapshot: (scope: SearchScope) => ["inbox-snapshot", scope] as const,
  repositoryCaptures: (repository: string) =>
    ["inbox", "captures", repository] as const,
  preferences: ["preferences"] as const,
  pins: ["pins"] as const,
  people: (logins: readonly string[]) => ["people", ...logins] as const,
  peopleSearch: (query: string) => ["people-search", query] as const,
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

  private readonly pullReads = new Limiter(PULL_READS)
  private readonly comparisons = new Limiter(2)

  /** Whether `error` may pass on its own: a rate limit, a server error or a dropped connection. */
  static isTransient(error: unknown): boolean {
    return (
      !(error instanceof HostError) ||
      error.status === null ||
      error.status === 429 ||
      error.status >= 500
    )
  }

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

  /** Every document of `collection`, or those whose key starts with `prefix`, in pages. */
  private async *documents<K extends string>(
    collection: Collection<K, JsonObject>,
    prefix?: string
  ) {
    let after: K | undefined
    for (;;) {
      const page = await collection.query({
        prefix: prefix || undefined,
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

  /**
   * The pull request as GitHub has it now, read a few at a time;
   * `null` when GitHub refuses it, and a rejection to retry when the failure
   * may pass, such as a rate limit.
   */
  async currentPull(repository: string, pull: number) {
    try {
      return await this.pullReads.run(() =>
        this.host.github(repository).pull(pull)
      )
    } catch (error) {
      if (DesignQa.isTransient(error)) throw error
      console.warn("Could not read a pull request from GitHub", error)
      return null
    }
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
    for await (const stored of this.documents(
      this.captures(repository),
      "pulls/"
    )) {
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
   * Page `page` of the open pull requests asking the person (or a team of
   * theirs) for review, or their own, or with `author` that login's, as
   * GitHub's search lists them, newest activity first: no captures read yet.
   */
  searchPage(
    scope: SearchScope,
    page: number,
    author?: string
  ): Promise<PullRequestSearch> {
    return author
      ? this.host.pulls.search({ role: "author", page, author })
      : this.host.pulls.search({
          role: scope === "mine" ? "author" : "review-requested",
          page,
        })
  }

  /** The last list a tab's search found, kept private to the person. */
  private snapshotStore() {
    return this.host.storage.collection<SearchScope, JsonObject>("inbox")
  }

  async snapshot(scope: SearchScope): Promise<PullRequestSummary[] | null> {
    const found = await this.snapshotStore().get(scope)
    return found ? InboxSnapshot.read(found.document) : null
  }

  async saveSnapshot(scope: SearchScope, document: JsonObject): Promise<void> {
    await this.snapshotStore().put(scope, document)
  }

  /** The pull requests the person pinned, kept private to them. */
  private pinStore() {
    return this.host.storage.collection<PinKey, JsonObject>("pins")
  }

  async pins(): Promise<Pin[]> {
    const pins: Pin[] = []
    for await (const stored of this.documents(this.pinStore())) {
      const pin = Pins.read(stored.document)
      if (pin) pins.push(pin)
    }
    return pins
  }

  async pin(pin: Pin): Promise<void> {
    await this.pinStore().put(
      Pins.key(pin.repository, pin.number),
      Pins.document(pin)
    )
  }

  async unpin(pr: PullRequestRef): Promise<void> {
    try {
      await this.pinStore().delete(Pins.key(repositoryOf(pr), pr.number))
    } catch (error) {
      if (
        error instanceof HostError &&
        error.status === 404 &&
        error.message === "document not found"
      )
        return
      throw error
    }
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

  static hidden(found: RepositoryCaptures | undefined, number: number) {
    const outcome = found?.outcomes.get(number)
    const run = found?.runs.get(number)
    const current =
      outcome &&
      (!run || Date.parse(outcome.created_at) > Date.parse(run.created_at))
        ? outcome
        : null
    if (current?.status === "skipped") return true
    if (run?.status === "complete" && (run.report?.scenarios.length ?? 0) > 0)
      return false
    if (current)
      return (
        current.status === "failed" ||
        (current.status === "blocked" &&
          !new CaptureAttempt(current).previewPending)
      )
    return (
      run?.status === "failed" ||
      run?.status === "complete" ||
      (run?.status === "blocked" && !CaptureAttempt.isPreviewPending(run.error))
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
   * Whether an inbox card reads its pull request from GitHub: search already
   * supplies an uncaptured one's identity and title, but a pin only what it
   * was when pinned.
   */
  static reads(
    pull: PullRequestSummary,
    found: RepositoryCaptures | undefined,
    pinned: boolean
  ): boolean {
    return pinned || Boolean(found?.runs.has(pull.number))
  }

  /**
   * A card: the pull request as GitHub has it now once `current` is read, with
   * its latest capture. Closed pull requests are eligible only when pinned.
   */
  static item(
    pull: PullRequestSummary,
    found: RepositoryCaptures | undefined,
    current: PullRequest | null | undefined,
    pinned: boolean
  ): InboxItem | null {
    if (DesignQa.hidden(found, pull.number)) return null
    if (current && current.state !== "open" && !pinned) return null
    return {
      repository: pull.repository,
      pull: current ?? pull,
      run: found?.runs.get(pull.number) ?? null,
      outcome: found?.outcomes.get(pull.number) ?? null,
    }
  }

  /** `pulls` without a pull request listed twice, as when it moved between search pages. */
  static unique(pulls: readonly PullRequestSummary[]): PullRequestSummary[] {
    return [
      ...new Map(
        pulls.map((pull) => [`${pull.repository}#${pull.number}`, pull])
      ).values(),
    ]
  }

  async runs(pr: PullRequestRef): Promise<PullRequestRuns> {
    const repository = repositoryOf(pr)
    const [runs, pull, outcomes] = await Promise.all([
      this.latestRuns(repository, pr.number, 20),
      this.currentPull(repository, pr.number).catch((error: unknown) => {
        console.warn("Could not read a pull request from GitHub", error)
        return null
      }),
      this.latestOutcomes(repository, pr.number),
    ])
    return {
      runs,
      pull,
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

  changeQuery(
    queryClient: QueryClient,
    target: RunMedia,
    { before, after }: ScenarioShots,
    enabled = true
  ) {
    return queryOptions({
      queryKey: keys.change(target, before ?? "", after ?? ""),
      queryFn: () =>
        this.comparisons.run(async () => {
          if (!before || !after) return null
          const [first, second] = await Promise.all(
            [before, after].map((name) =>
              queryClient.fetchQuery({
                queryKey: keys.media(target, name),
                queryFn: () => this.media(target, name),
                staleTime: Infinity,
                gcTime: 60_000,
              })
            )
          )
          return first && second ? PixelChange.between(first, second) : null
        }),
      enabled: enabled && Boolean(before && after),
      staleTime: Infinity,
      gcTime: 60_000,
    })
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

const searchQuery = (qa: DesignQa, scope: SearchScope, author?: string) =>
  infiniteQueryOptions({
    queryKey: keys.inboxSearch(scope, author),
    queryFn: ({ pageParam }) => qa.searchPage(scope, pageParam, author),
    initialPageParam: 1,
    getNextPageParam: (last: PullRequestSearch) => last.nextPage ?? undefined,
    staleTime: 60_000,
  })

const pinsQuery = (qa: DesignQa) =>
  queryOptions({ queryKey: keys.pins, queryFn: () => qa.pins() })

/**
 * A card's pull request as GitHub has it now, `null` when GitHub won't say.
 * A pin's is read by the Pinned tab's list and its card alike.
 */
const currentPullQuery = (
  qa: DesignQa,
  pull: PullRequestSummary,
  pinned: boolean
) =>
  queryOptions({
    queryKey: pinned
      ? keys.pinnedPull(pull.repository, pull.number)
      : keys.inboxPull(pull),
    queryFn: () => qa.currentPull(pull.repository, pull.number),
    retry: (failures: number, error: Error) =>
      failures < 3 && DesignQa.isTransient(error),
    staleTime: 60_000,
    // As the search does: a head can move without GitHub's search noticing yet.
    refetchInterval: 5 * 60_000,
  })

/**
 * An inbox tab's pull requests: the person's pins, or GitHub's search, shown
 * from its first page while the rest load. Until the first page comes, the
 * person's own tabs show what the search found last time, and remember this
 * one once every page has loaded. The People tab searches `author`'s, and
 * lists nothing until someone is picked.
 */
export function useInboxSearch(
  scope: InboxScope,
  author?: string
): InboxSearch {
  const qa = useDesignQa()
  const queryClient = useQueryClient()
  const people = scope === "people"
  const searched: SearchScope | null =
    scope === "pinned" || (people && !author) ? null : people ? "mine" : scope
  const remembers = searched !== null && !people
  const pins = useQuery({ ...pinsQuery(qa), enabled: scope === "pinned" })
  const pinned = (pins.data ?? []).map((pin) => Pins.summary(pin, null))
  const pinReads = useQueries({
    queries: pinned.map((pull) => ({
      ...currentPullQuery(qa, pull, true),
      enabled: scope === "pinned",
    })),
  })
  const snapshot = useQuery({
    queryKey: keys.inboxSnapshot(searched ?? "mine"),
    queryFn: () => qa.snapshot(searched ?? "mine"),
    enabled: remembers,
    staleTime: Infinity,
    retry: false,
  })
  const search = useInfiniteQuery({
    ...searchQuery(qa, searched ?? "mine", people ? author : undefined),
    enabled: searched !== null,
    // Review requests come and go on GitHub, which tells the app nothing.
    refetchInterval: 5 * 60_000,
    refetchOnWindowFocus: true,
  })
  const { hasNextPage, isFetchingNextPage, isError, fetchNextPage } = search
  const partial = search.isFetchNextPageError
  useEffect(() => {
    if (searched && hasNextPage && !isFetchingNextPage && !isError)
      void fetchNextPage()
  }, [searched, hasNextPage, isFetchingNextPage, isError, fetchNextPage])
  const pages = search.data?.pages
  const live = pages && DesignQa.unique(pages.flatMap(({ pulls }) => pulls))
  const incomplete =
    partial || (pages?.some((page) => page.incomplete) ?? false)
  const finished =
    live && !hasNextPage && !incomplete
      ? JSON.stringify(InboxSnapshot.document(live))
      : null
  const remembered =
    snapshot.data === undefined
      ? undefined
      : JSON.stringify(InboxSnapshot.document(snapshot.data ?? []))
  useEffect(() => {
    if (!searched || !remembers || !finished || remembered === undefined) return
    if (finished === remembered) return
    const document = JSON.parse(finished) as JsonObject
    qa.saveSnapshot(searched, document).then(
      () =>
        queryClient.setQueryData(
          keys.inboxSnapshot(searched),
          InboxSnapshot.read(document)
        ),
      (error: unknown) =>
        console.warn("Could not remember the inbox's search", error)
    )
  }, [qa, queryClient, searched, remembers, finished, remembered])

  if (people && !author)
    return {
      pulls: [],
      remembered: false,
      incomplete: false,
      fetching: false,
      more: false,
      error: null,
    }
  if (searched === null)
    return {
      pulls: pins.data?.map((pin, index) =>
        Pins.summary(pin, pinReads[index]?.data ?? null)
      ),
      remembered: false,
      incomplete: false,
      fetching: pins.isFetching,
      more: false,
      error: pins.error,
    }
  const kept = remembers ? snapshot.data : undefined
  return {
    pulls: live ?? kept ?? undefined,
    remembered: !live && Boolean(kept),
    incomplete,
    fetching: search.isFetching,
    more: Boolean(live) && hasNextPage && !isError,
    error: partial ? null : search.error,
  }
}

/**
 * Starts the other tabs' first page of search and the person's pins, so
 * switching to one shows it at once.
 */
export function usePrefetchInbox(current: InboxScope) {
  const qa = useDesignQa()
  const queryClient = useQueryClient()
  useEffect(() => {
    for (const scope of ["review-requested", "mine"] as const)
      if (scope !== current)
        void queryClient.prefetchInfiniteQuery(searchQuery(qa, scope))
    void queryClient.prefetchQuery(pinsQuery(qa))
  }, [qa, queryClient, current])
}

/**
 * Whether the person pinned `pr`, and pinning or unpinning it, shown at once,
 * in the Pinned tab too, and undone with a toast if storage refuses.
 */
export function usePin(pr: PullRequestRef, title: string) {
  const host = usePluginHost()
  const qa = DesignQa.of(host)
  const queryClient = useQueryClient()
  const pins = useQuery(pinsQuery(qa))
  const key = Pins.key(repositoryOf(pr), pr.number)
  const other = (pin: Pin) => Pins.key(pin.repository, pin.number) !== key
  const pinned = pins.data?.some((pin) => !other(pin)) ?? false
  const toggle = useMutation({
    mutationFn: (next: Pin | null) => (next ? qa.pin(next) : qa.unpin(pr)),
    onMutate: async (next) => {
      await queryClient.cancelQueries({ queryKey: keys.pins })
      const previous = queryClient.getQueryData<Pin[]>(keys.pins)
      queryClient.setQueryData<Pin[]>(keys.pins, (current = []) => [
        ...current.filter(other),
        ...(next ? [next] : []),
      ])
      return { previous }
    },
    onError: (error, next, context) => {
      queryClient.setQueryData(keys.pins, context?.previous)
      void host.ui.toast({
        title: next ? "Could not pin this PR" : "Could not unpin this PR",
        description: errorMessage(error),
        intent: "error",
      })
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: keys.pins }),
  })
  return {
    pinned,
    ready: !pins.isPending,
    toggle: () =>
      toggle.mutate(
        pinned
          ? null
          : {
              repository: repositoryOf(pr).toLowerCase(),
              number: pr.number,
              title,
              pinnedAt: new Date().toISOString(),
            }
      ),
  }
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
      error:
        results.find((result) => result.error && !isForbidden(result.error))
          ?.error ?? null,
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

/**
 * One page's cards at once, from search and captures, each filled in as GitHub
 * answers for those it reads again; the next page's are read ahead, so turning
 * to it shows them at once.
 */
export function useInboxPage(
  scope: InboxScope,
  shown: readonly PullRequestSummary[],
  upcoming: readonly PullRequestSummary[],
  captures: ReadonlyMap<string, RepositoryCaptures>
): InboxPage {
  const qa = useDesignQa()
  const pinned = scope === "pinned"
  const reads = useQueries({
    queries: [...shown, ...upcoming].map((pull) => ({
      ...currentPullQuery(qa, pull, pinned),
      enabled: DesignQa.reads(pull, captures.get(pull.repository), pinned),
    })),
  })
  const items = shown.flatMap((pull, index) => {
    const item = DesignQa.item(
      pull,
      captures.get(pull.repository),
      reads[index]?.data,
      pinned
    )
    return item ? [item] : []
  })
  return {
    items,
    incomplete: reads
      .slice(0, shown.length)
      .some(({ data, isError }) => data === null || isError),
    fetching: reads.slice(0, shown.length).some(({ isFetching }) => isFetching),
  }
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
  shots: ScenarioShots,
  enabled = true
) {
  const qa = useDesignQa()
  const queryClient = useQueryClient()
  return useQuery(qa.changeQuery(queryClient, target, shots, enabled))
}

function useVisibleRuns(
  entries: readonly { target: RunMedia; run: FrontendReviewRun }[]
) {
  const qa = useDesignQa()
  const queryClient = useQueryClient()
  const comparisons = entries.flatMap(({ target, run }, entry) =>
    (run.report?.scenarios ?? []).flatMap((scenario) =>
      scenario.status !== "complete"
        ? []
        : PixelChange.shots(scenario).map((shots) => ({
            entry,
            scenario,
            query: qa.changeQuery(queryClient, target, shots),
          }))
    )
  )
  const results = useQueries({
    queries: comparisons.map(({ query }) => query),
  })
  const changes = entries.map(
    () => new Map<FrontendScenario, (PixelChange | null | undefined)[]>()
  )
  comparisons.forEach(({ entry, scenario }, index) => {
    const found = changes[entry]
    const result = results[index]
    if (!found) return
    const values = found.get(scenario) ?? []
    values.push(result?.data)
    found.set(scenario, values)
  })
  return {
    runs: entries.map(({ run }, index) =>
      PixelChange.filter(run, changes[index] ?? new Map())
    ),
    pending: results.some((result) => result.isLoading),
  }
}

export function useVisibleRun(
  target: RunMedia,
  run: FrontendReviewRun | null | undefined
): FrontendReviewRun | null {
  return useVisibleRuns(run ? [{ target, run }] : []).runs[0] ?? null
}

export function useVisibleCaptures(
  byRepository: ReadonlyMap<string, RepositoryCaptures>,
  pulls: readonly PullRequestSummary[]
) {
  const entries = pulls.flatMap(({ repository, number }) => {
    const run = byRepository.get(repository)?.runs.get(number)
    return run
      ? [{ target: { repository, pull: number, run: run.id }, run }]
      : []
  })
  const visible = useVisibleRuns(entries)
  const filtered = new Map(
    [...byRepository].map(([repository, captures]) => [
      repository,
      { ...captures, runs: new Map(captures.runs) },
    ])
  )
  entries.forEach(({ target }, index) => {
    const run = visible.runs[index]
    if (run) filtered.get(target.repository)?.runs.set(target.pull, run)
  })
  return { byRepository: filtered, pending: visible.pending }
}

/** Names and pictures of GitHub `logins`, by lowercased login. */
export function usePeople(logins: readonly (string | null)[]) {
  const host = usePluginHost()
  const wanted = [
    ...new Set(logins.flatMap((login) => (login ? [login.toLowerCase()] : []))),
  ].sort()
  const people = useQuery({
    queryKey: keys.people(wanted),
    queryFn: () => host.people.get(wanted),
    enabled: wanted.length > 0,
    staleTime: Infinity,
  })
  return new Map<string, Person>(
    (people.data ?? []).map((person) => [person.login.toLowerCase(), person])
  )
}

/** People Robin knows on GitHub whose name or login matches `query`, closest first. */
export function usePeopleSearch(query: string) {
  const host = usePluginHost()
  const text = query.trim()
  return useQuery({
    queryKey: keys.peopleSearch(text.toLowerCase()),
    queryFn: () => host.people.search(text),
    staleTime: 5 * 60_000,
    placeholderData: keepPreviousData,
  })
}

/** Names the web UI's tab after what is open; `null` leaves it named Design QA. */
export function useTitle(title: string | null) {
  const host = usePluginHost()
  useEffect(() => {
    void host.ui.setTitle(title)
  }, [host, title])
}
