import {
  type InfiniteData,
  type QueryClient,
  useInfiniteQuery,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { BugBeetleIcon, FlagIcon } from "@phosphor-icons/react"
import { type ReactNode, useEffect, useState } from "react"
import { toast } from "sonner"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { MultiSelect } from "@/components/ui/multi-select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  api,
  type MergeMethod,
  type OpenPullRequest,
  type OpenPullRequestsPayload,
  type ReviewSummary,
} from "@/lib/api"
import { useRepos } from "@/lib/profile"
import { cn } from "@/lib/utils"
import {
  readPreferredMergeMethod,
  writePreferredMergeMethod,
} from "./mergeMethod"
import { reviewStatuses, type ReviewsSearch, type ReviewSort } from "./search"
import { PullRequestLinks } from "./PullRequestLinks"

const control =
  "rounded-md border border-border bg-background px-3 py-2 text-xs text-foreground"
const pageSize = 10
const mergeMethods: readonly MergeMethod[] = ["squash", "merge", "rebase"]
const mergeMethodLabels: Record<MergeMethod, string> = {
  squash: "Squash merge",
  merge: "Merge commit",
  rebase: "Rebase merge",
}

type BulkAction = "close" | "fix" | "merge" | "ready"

interface BulkRequest {
  action: BulkAction
  pullRequests: OpenPullRequest[]
  method?: MergeMethod
}

interface BulkOutcome {
  action: BulkAction
  succeeded: OpenPullRequest[]
  failures: Array<{ key: string; message: string }>
}

function pullRequestKey(pr: { repo: string; number: number }) {
  return `${pr.repo}#${pr.number}`
}

function isFixable(pr: OpenPullRequest) {
  return (
    pr.mergeable === false || pr.mergeState === "dirty" || pr.ci === "failing"
  )
}

// Approved is a review verdict, so it can stand while GitHub is still deciding
// whether the branch merges or while the checks could not be read. Merging
// needs both of those answers: GitHub blocks only required checks, so an
// unread check could be a failing one. `unstable` is GitHub saying it will
// accept the merge and only checks it does not require are unhappy.
function isMergeable(pr: OpenPullRequest) {
  if (pr.mergeable !== true || !pr.headSha) return false
  if (pr.mergeState === "unstable") return pr.reviewDecision === "approved"
  return (
    overallStatus(pr) === "Approved" &&
    (pr.ci === "passing" || pr.ci === "none")
  )
}

async function runBulkAction(
  action: BulkAction,
  pr: OpenPullRequest,
  method: MergeMethod | undefined
) {
  if (action === "close") {
    const result = await api.closePullRequest(pr)
    if (!result.closed) throw new Error("GitHub did not confirm the close.")
  } else if (action === "ready") {
    const result = await api.markPullRequestReady(pr)
    if (!result.ready)
      throw new Error("GitHub did not confirm the ready for review.")
  } else if (action === "merge") {
    if (!method) throw new Error("Choose a merge method.")
    const result = await api.mergePullRequest(pr, method)
    if (!result.merged) throw new Error("GitHub did not confirm the merge.")
  } else {
    await api.fixPullRequest(pr)
  }
}

function forgetPullRequest(
  queryClient: QueryClient,
  login: string,
  pr: { repo: string; number: number }
) {
  queryClient.setQueryData(["my-pr-details", login, pr.repo, pr.number], null)
  queryClient.setQueriesData<InfiniteData<OpenPullRequestsPayload>>(
    { queryKey: ["my-pull-requests", login] },
    (data) =>
      data
        ? {
            ...data,
            pages: data.pages.map((loaded) => ({
              ...loaded,
              pullRequests: loaded.pullRequests.filter(
                (row) => row.repo !== pr.repo || row.number !== pr.number
              ),
            })),
          }
        : data
  )
}

function refreshPullRequest(
  queryClient: QueryClient,
  login: string,
  pr: { repo: string; number: number }
) {
  void queryClient.invalidateQueries({
    queryKey: ["my-pr-details", login, pr.repo, pr.number],
  })
}

function useOpenPullRequests(
  login: string,
  repo: string[],
  sort: ReviewSort,
  direction: "asc" | "desc"
) {
  return useInfiniteQuery({
    queryKey: ["my-pull-requests", login, repo, sort, direction],
    queryFn: ({ pageParam }) =>
      api.myPullRequests(repo.join(","), sort, direction, pageParam),
    initialPageParam: 1,
    getNextPageParam: (last) => last.nextPage ?? undefined,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  })
}

function overallStatus(pr: OpenPullRequest) {
  if (pr.detailsLoading) return "Loading…"
  if (pr.detailsError) return "Could not load status"
  if (pr.draft) return "Draft"
  if (pr.mergeable === false || pr.mergeState === "dirty") return "Conflicted"
  if (pr.ci === "failing") return "Failing"
  if (pr.ci === "pending") return "Pending"
  if (
    !pr.statusAvailable ||
    (pr.ci === "unknown" && pr.reviewDecision === null)
  )
    return "Status unavailable"
  if (pr.reviewDecision === "changes_requested") return "Changes Requested"
  if (pr.reviewDecision === "approved") return "Approved"
  return "Reviewable"
}

const statusTones: Record<string, string> = {
  Conflicted: "border-destructive/30 bg-destructive/10 text-destructive",
  Failing: "border-destructive/30 bg-destructive/10 text-destructive",
  "Changes Requested":
    "border-destructive/30 bg-destructive/10 text-destructive",
  Approved:
    "border-emerald-600/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  Pending:
    "border-amber-600/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  Reviewable: "border-sky-600/30 bg-sky-500/10 text-sky-700 dark:text-sky-400",
}

function StatusPill({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium",
        statusTones[status] ?? "border-border bg-muted text-muted-foreground"
      )}
    >
      {status}
    </span>
  )
}

// A draft still has to show a conflict or a failing check: they are what the
// author has to act on, and they outlive the draft flag.
function statusLabels(pr: OpenPullRequest): string[] {
  const status = overallStatus(pr)
  if (status !== "Draft") return [status]
  return [
    status,
    ...(pr.mergeable === false || pr.mergeState === "dirty"
      ? ["Conflicted"]
      : []),
    ...(pr.ci === "failing" ? ["Failing"] : []),
  ]
}

function MergePullRequest({
  pr,
  onMerged,
}: {
  pr: OpenPullRequest
  onMerged: () => void
}) {
  const [method, setMethod] = useState<MergeMethod | "">(
    () => readPreferredMergeMethod() ?? ""
  )
  const merge = useMutation({
    mutationFn: async () => {
      if (!method) throw new Error("Choose a merge method.")
      const result = await api.mergePullRequest(pr, method)
      if (!result.merged) throw new Error("GitHub did not confirm the merge.")
      writePreferredMergeMethod(method)
    },
    onSuccess: () => {
      toast.success(`Merged ${pr.repo}#${pr.number}`)
      onMerged()
    },
    onError: (error) =>
      toast.error(`Could not merge ${pr.repo}#${pr.number}`, {
        description: error.message,
      }),
    retry: false,
  })
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        className={control}
        aria-label={`Merge method for PR #${pr.number}`}
        value={method}
        disabled={merge.isPending}
        onChange={(event) => {
          const value = event.target.value
          if (
            value === "squash" ||
            value === "merge" ||
            value === "rebase" ||
            value === ""
          )
            setMethod(value)
        }}
      >
        <option value="" disabled>
          Merge method
        </option>
        <option value="squash">Squash merge</option>
        <option value="merge">Merge commit</option>
        <option value="rebase">Rebase merge</option>
      </select>
      <Button
        size="sm"
        variant="outline"
        disabled={!method || !pr.headSha || merge.isPending || merge.isSuccess}
        aria-live="polite"
        onClick={() => merge.mutate()}
      >
        {merge.isPending ? "Merging…" : merge.isError ? "Retry merge" : "Merge"}
      </Button>
      {merge.error && (
        <p role="alert" className="text-destructive">
          {merge.error.message}
        </p>
      )}
    </div>
  )
}

function FixPullRequest({ pr, login }: { pr: OpenPullRequest; login: string }) {
  const thread = useQuery({
    queryKey: ["pr-thread-status", login, pr.repo, pr.number],
    queryFn: () => api.pullRequestThreadStatus(pr.repo, pr.number),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  })
  const fix = useMutation({
    mutationFn: () => api.fixPullRequest(pr),
    onSuccess: (result) =>
      toast.success(
        `${result.already_running ? "Fix already in progress for" : "Fix queued for"} ${pr.repo}#${pr.number}`
      ),
    onError: (error) =>
      toast.error(`Could not queue fix for ${pr.repo}#${pr.number}`, {
        description: error.message,
      }),
  })
  return (
    <div>
      <Button
        size="sm"
        variant="outline"
        disabled={
          thread.isPending ||
          thread.isError ||
          thread.data?.running ||
          fix.isPending ||
          fix.isSuccess
        }
        aria-live="polite"
        onClick={() => fix.mutate()}
      >
        {thread.data?.running || fix.data?.already_running
          ? "Fix in progress"
          : thread.isPending
            ? "Checking…"
            : thread.isError
              ? "Fix unavailable"
              : fix.isPending
                ? "Queuing fix…"
                : fix.isSuccess
                  ? "Fix queued"
                  : fix.isError
                    ? "Retry fix"
                    : "Fix"}
      </Button>
      {thread.error && (
        <p role="alert" className="mt-1 text-destructive">
          {thread.error.message}
        </p>
      )}
      {fix.error && (
        <p role="alert" className="mt-1 text-destructive">
          {fix.error.message}
        </p>
      )}
    </div>
  )
}

function MarkPullRequestReady({
  pr,
  onReady,
}: {
  pr: OpenPullRequest
  onReady: () => void
}) {
  const ready = useMutation({
    mutationFn: async () => {
      const result = await api.markPullRequestReady(pr)
      if (!result.ready)
        throw new Error("GitHub did not confirm the ready for review.")
    },
    onSuccess: () => {
      toast.success(`Marked ${pr.repo}#${pr.number} ready for review`)
      onReady()
    },
    onError: (error) =>
      toast.error(`Could not mark ${pr.repo}#${pr.number} ready`, {
        description: error.message,
      }),
    retry: false,
  })
  return (
    <div>
      <Button
        size="sm"
        variant="outline"
        disabled={ready.isPending || ready.isSuccess}
        aria-live="polite"
        onClick={() => ready.mutate()}
      >
        {ready.isPending
          ? "Marking ready…"
          : ready.isSuccess
            ? "Marked ready"
            : ready.isError
              ? "Retry mark ready"
              : "Mark ready"}
      </Button>
      {ready.error && (
        <p role="alert" className="mt-1 text-destructive">
          {ready.error.message}
        </p>
      )}
    </div>
  )
}

function BulkCloseDialog({
  pullRequests,
  onCancel,
  onConfirm,
}: {
  pullRequests: OpenPullRequest[]
  onCancel: () => void
  onConfirm: () => void
}) {
  const listed = pullRequests.slice(0, 10)
  return (
    <AlertDialog
      open
      onOpenChange={(open) => {
        if (!open) onCancel()
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            Close {pullRequests.length} pull request
            {pullRequests.length === 1 ? "" : "s"}?
          </AlertDialogTitle>
          <AlertDialogDescription>
            GitHub closes them without merging.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <ul className="max-h-48 overflow-y-auto font-mono text-xs">
          {listed.map((pr) => (
            <li key={pullRequestKey(pr)}>{pullRequestKey(pr)}</li>
          ))}
          {pullRequests.length > listed.length && (
            <li>+{pullRequests.length - listed.length} more</li>
          )}
        </ul>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}>
            Close pull requests
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

function ClosePullRequest({
  pr,
  onClosed,
}: {
  pr: OpenPullRequest
  onClosed: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  const close = useMutation({
    mutationFn: async () => {
      const result = await api.closePullRequest(pr)
      if (!result.closed) throw new Error("GitHub did not confirm the close.")
    },
    onSuccess: () => {
      toast.success(`Closed ${pr.repo}#${pr.number}`)
      onClosed()
    },
    onError: (error) =>
      toast.error(`Could not close ${pr.repo}#${pr.number}`, {
        description: error.message,
      }),
    retry: false,
  })
  return (
    <div>
      <Button
        size="sm"
        variant="outline"
        disabled={close.isPending || close.isSuccess}
        aria-live="polite"
        onClick={() => setConfirming(true)}
      >
        {close.isPending ? "Closing…" : close.isError ? "Retry close" : "Close"}
      </Button>
      {confirming && (
        <BulkCloseDialog
          pullRequests={[pr]}
          onCancel={() => setConfirming(false)}
          onConfirm={() => {
            setConfirming(false)
            close.mutate()
          }}
        />
      )}
      {close.error && (
        <p role="alert" className="mt-1 text-destructive">
          {close.error.message}
        </p>
      )}
    </div>
  )
}

function BulkMergeDialog({
  pullRequests,
  onCancel,
  onConfirm,
}: {
  pullRequests: OpenPullRequest[]
  onCancel: () => void
  onConfirm: (method: MergeMethod) => void
}) {
  const [choice, setChoice] = useState<MergeMethod | "">(
    () => readPreferredMergeMethod() ?? ""
  )
  const repos = [...new Set(pullRequests.map((pr) => pr.repo))]
  const settings = useQueries({
    queries: repos.map((repo) => ({
      queryKey: ["repo-merge-methods", repo],
      queryFn: () => api.repoMergeMethods(repo),
      staleTime: Infinity,
      retry: false,
    })),
  })
  const loading = settings.some((setting) => setting.isPending)
  const failure = settings.find((setting) => setting.error)?.error
  const shared = mergeMethods.filter((method) =>
    settings.every((setting) => setting.data?.mergeMethods.includes(method))
  )
  const only = shared.length === 1 ? shared[0] : undefined
  // A remembered method the selected repositories do not all allow is no choice.
  const method =
    only ?? (shared.includes(choice as MergeMethod) ? choice : undefined)
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onCancel()
      }}
    >
      <DialogContent showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>
            Merge {pullRequests.length} pull request
            {pullRequests.length === 1 ? "" : "s"}?
          </DialogTitle>
          <DialogDescription>
            {pullRequests.map(pullRequestKey).join(", ")}
          </DialogDescription>
        </DialogHeader>
        {loading ? (
          <p role="status">Loading merge settings…</p>
        ) : failure ? (
          <p role="alert" className="text-destructive">
            {failure.message}
          </p>
        ) : shared.length === 0 ? (
          <p role="alert">The selected repositories share no merge method.</p>
        ) : only ? (
          <p>Merge method: {mergeMethodLabels[only]}</p>
        ) : (
          <select
            className={control}
            aria-label="Merge method"
            value={choice}
            onChange={(event) => {
              const value = event.target.value
              if (mergeMethods.some((option) => option === value))
                setChoice(value as MergeMethod)
            }}
          >
            <option value="" disabled>
              Choose a merge method
            </option>
            {shared.map((option) => (
              <option key={option} value={option}>
                {mergeMethodLabels[option]}
              </option>
            ))}
          </select>
        )}
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={onCancel}>
            Cancel
          </Button>
          <Button
            size="sm"
            disabled={!method}
            onClick={() => {
              if (method) onConfirm(method)
            }}
          >
            Merge pull requests
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function BulkActions({
  selected,
  login,
  onClear,
  onSettled,
}: {
  selected: OpenPullRequest[]
  login: string
  onClear: () => void
  onSettled: (succeeded: OpenPullRequest[]) => void
}) {
  const queryClient = useQueryClient()
  const [prompt, setPrompt] = useState<"close" | "merge" | null>(null)
  const bulk = useMutation({
    mutationFn: async ({
      action,
      pullRequests,
      method,
    }: BulkRequest): Promise<BulkOutcome> => {
      const succeeded: OpenPullRequest[] = []
      const failures: BulkOutcome["failures"] = []
      for (const pr of pullRequests) {
        try {
          await runBulkAction(action, pr, method)
          succeeded.push(pr)
        } catch (error) {
          failures.push({
            key: pullRequestKey(pr),
            message: error instanceof Error ? error.message : String(error),
          })
        }
      }
      return { action, succeeded, failures }
    },
    onSuccess: ({ action, succeeded, failures }) => {
      if (action === "fix")
        void queryClient.invalidateQueries({
          queryKey: ["pr-thread-status", login],
        })
      else if (action === "ready")
        for (const pr of succeeded) refreshPullRequest(queryClient, login, pr)
      else for (const pr of succeeded) forgetPullRequest(queryClient, login, pr)
      onSettled(succeeded)
      const total = succeeded.length + failures.length
      const counted =
        failures.length === 0
          ? `${total} pull request${total === 1 ? "" : "s"}`
          : `${succeeded.length} of ${total} pull request${total === 1 ? "" : "s"}`
      const message =
        action === "close"
          ? `Closed ${counted}`
          : action === "merge"
            ? `Merged ${counted}`
            : action === "ready"
              ? `Marked ${counted} ready for review`
              : `Queued fixes for ${counted}`
      if (failures.length === 0) toast.success(message)
      else
        toast.error(message, {
          description: failures
            .map((failure) => `${failure.key}: ${failure.message}`)
            .join("\n"),
        })
    },
    retry: false,
  })
  const active = bulk.isPending ? bulk.variables.action : null
  const fixable = selected.every((pr) => isFixable(pr) && !pr.detailsLoading)
  const mergeable = selected.every(isMergeable)
  const drafts = selected.every((pr) => pr.draft === true)
  return (
    <div
      role="group"
      aria-label="Bulk pull request actions"
      className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2 text-xs"
    >
      <span aria-live="polite" className="font-medium">
        {selected.length} selected
      </span>
      <Button size="sm" variant="ghost" onClick={onClear}>
        Clear selection
      </Button>
      <Button
        size="sm"
        variant="outline"
        disabled={bulk.isPending}
        onClick={() => setPrompt("close")}
      >
        {active === "close" ? "Closing…" : "Close"}
      </Button>
      <Button
        size="sm"
        variant="outline"
        disabled={bulk.isPending || !fixable}
        title={
          fixable
            ? undefined
            : "Every selected PR must be conflicted or failing"
        }
        onClick={() => bulk.mutate({ action: "fix", pullRequests: selected })}
      >
        {active === "fix" ? "Queuing fixes…" : "Fix"}
      </Button>
      <Button
        size="sm"
        variant="outline"
        disabled={bulk.isPending || !mergeable}
        title={
          mergeable
            ? undefined
            : "Every selected PR must be approved and passing"
        }
        onClick={() => setPrompt("merge")}
      >
        {active === "merge" ? "Merging…" : "Merge"}
      </Button>
      <Button
        size="sm"
        variant="outline"
        disabled={bulk.isPending || !drafts}
        title={drafts ? undefined : "Every selected PR must be a draft"}
        onClick={() => bulk.mutate({ action: "ready", pullRequests: selected })}
      >
        {active === "ready" ? "Marking ready…" : "Mark ready"}
      </Button>
      {prompt === "close" && (
        <BulkCloseDialog
          pullRequests={selected}
          onCancel={() => setPrompt(null)}
          onConfirm={() => {
            setPrompt(null)
            bulk.mutate({ action: "close", pullRequests: selected })
          }}
        />
      )}
      {prompt === "merge" && (
        <BulkMergeDialog
          pullRequests={selected}
          onCancel={() => setPrompt(null)}
          onConfirm={(method) => {
            setPrompt(null)
            writePreferredMergeMethod(method)
            bulk.mutate({ action: "merge", pullRequests: selected, method })
          }}
        />
      )}
    </div>
  )
}

function ReviewIndicators({ review }: { review: ReviewSummary }) {
  return (
    <a
      href={`/agents/reviews/${encodeURIComponent(review.owner)}/${encodeURIComponent(review.repo)}/${review.number}`}
      className="inline-flex flex-col gap-1.5 hover:underline"
      aria-label={`Open review: ${review.counts.bugs} bugs, ${review.counts.flags} flags`}
    >
      <span className="flex gap-3">
        <span
          className={cn(
            "inline-flex items-center gap-1",
            review.counts.bugs > 0 && "text-destructive"
          )}
        >
          <BugBeetleIcon aria-hidden="true" className="size-3.5" />
          {review.counts.bugs} bugs
        </span>
        <span className="inline-flex items-center gap-1">
          <FlagIcon aria-hidden="true" className="size-3.5" />
          {review.counts.flags} flags
        </span>
      </span>
      {review.status === "running" && (
        <span className="text-amber-700 dark:text-amber-400">Reviewing…</span>
      )}
      {review.status === "error" && (
        <span className="text-destructive">Review failed</span>
      )}
    </a>
  )
}

function dateLabel(value: string | null) {
  return value
    ? new Date(value).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "Unavailable"
}

function Diffstat({ pr }: { pr: OpenPullRequest }) {
  if (pr.detailsLoading) return <Skeleton className="h-4 w-20" />
  if (pr.additions === null || pr.deletions === null) return <span>—</span>
  const total = pr.additions + pr.deletions
  return (
    <div
      className="min-w-24"
      aria-label={`${pr.additions} lines added, ${pr.deletions} lines deleted`}
    >
      <div className="flex gap-2 font-mono text-xs tabular-nums">
        <span className="text-emerald-600 dark:text-emerald-400">
          +{pr.additions.toLocaleString()}
        </span>
        <span className="text-destructive">
          −{pr.deletions.toLocaleString()}
        </span>
      </div>
      <div
        className="mt-1.5 flex h-1 w-20 overflow-hidden rounded-full bg-muted"
        aria-hidden="true"
      >
        {total > 0 && (
          <>
            <span
              className="bg-emerald-500"
              style={{ width: `${(pr.additions / total) * 100}%` }}
            />
            <span
              className="bg-destructive"
              style={{ width: `${(pr.deletions / total) * 100}%` }}
            />
          </>
        )}
      </div>
    </div>
  )
}

function PullRequestCard({
  pr,
  login,
  selected,
  onSelect,
  review,
  onRemoved,
  onReady,
}: {
  pr: OpenPullRequest
  login: string
  selected: boolean
  onSelect: (include: boolean) => void
  review: ReactNode
  onRemoved: () => void
  onReady: () => void
}) {
  return (
    <li
      className={cn(
        "rounded-lg border bg-card p-4",
        selected ? "border-primary bg-primary/5" : "border-border"
      )}
    >
      <div className="flex gap-3">
        <input
          type="checkbox"
          className="mt-0.5 shrink-0 self-start"
          aria-label={`Select PR #${pr.number} in ${pr.repo}`}
          checked={selected}
          onChange={(event) => onSelect(event.target.checked)}
        />
        <div className="min-w-0 flex-1 space-y-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs text-muted-foreground">
              <span>
                {pr.repo}{" "}
                <span className="font-mono tabular-nums">#{pr.number}</span>
              </span>
              {statusLabels(pr).map((status) => (
                <StatusPill key={status} status={status} />
              ))}
            </div>
            <h3 className="mt-1 text-sm font-medium break-words">{pr.title}</h3>
          </div>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted-foreground">
            <Diffstat pr={pr} />
            {review}
            <span>
              Updated{" "}
              <time
                dateTime={pr.updatedAt ?? undefined}
                title={pr.updatedAt ?? undefined}
              >
                {dateLabel(pr.updatedAt)}
              </time>
            </span>
            <span>
              Opened{" "}
              <time
                dateTime={pr.createdAt ?? undefined}
                title={pr.createdAt ?? undefined}
              >
                {dateLabel(pr.createdAt)}
              </time>
            </span>
            {!pr.statusAvailable && !pr.detailsLoading && (
              <span className="text-amber-700 dark:text-amber-400">
                Live PR status unavailable
              </span>
            )}
          </div>
          {(pr.failingChecks.length > 0 || pr.pendingChecks.length > 0) && (
            <div className="space-y-1 text-xs">
              {pr.failingChecks.length > 0 && (
                <p className="text-destructive">
                  {pr.failingChecks.slice(0, 3).map((name, index) => (
                    <span key={`${name}-${index}`}>
                      {index > 0 && <span aria-hidden="true"> · </span>}
                      {name}
                    </span>
                  ))}
                </p>
              )}
              {pr.failingChecks.length > 3 && (
                <details className="text-destructive">
                  <summary className="cursor-pointer">
                    +{pr.failingChecks.length - 3} more
                  </summary>
                  <ul className="mt-1 space-y-1">
                    {pr.failingChecks.slice(3).map((name, index) => (
                      <li key={`${name}-${index}`}>{name}</li>
                    ))}
                  </ul>
                </details>
              )}
              {pr.pendingChecks.length > 0 && (
                <details className="text-muted-foreground">
                  <summary className="cursor-pointer">
                    {pr.pendingChecks.length} pending
                  </summary>
                  <ul className="mt-1">
                    {pr.pendingChecks.map((name, index) => (
                      <li key={`${name}-${index}`}>{name}</li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-2">
              {isFixable(pr) && <FixPullRequest pr={pr} login={login} />}
              {isMergeable(pr) && (
                <MergePullRequest pr={pr} onMerged={onRemoved} />
              )}
              {pr.draft === true && (
                <MarkPullRequestReady pr={pr} onReady={onReady} />
              )}
              <ClosePullRequest pr={pr} onClosed={onRemoved} />
            </div>
            <PullRequestLinks
              repo={pr.repo}
              number={pr.number}
              title={pr.title}
            />
          </div>
        </div>
      </div>
    </li>
  )
}

export function MyPullRequests({
  login,
  filters,
  onFiltersChange,
}: {
  login: string
  filters: ReviewsSearch
  onFiltersChange: (changes: Partial<ReviewsSearch>, replace?: boolean) => void
}) {
  const {
    repo = [],
    q: search = "",
    status: filter,
    sort = "updatedAt",
    page = 0,
    direction = "desc",
  } = filters
  const toggleSort = (next: ReviewSort) =>
    onFiltersChange({
      page: undefined,
      sort: next,
      direction: sort === next && direction === "asc" ? "desc" : "asc",
    })
  const queryClient = useQueryClient()
  const [selection, setSelection] = useState<Set<string>>(new Set())
  const query = useOpenPullRequests(login, repo, sort, direction)
  const knownRepos = useRepos()
  const pages = query.data?.pages ?? []
  const latest = pages.at(-1)
  const rows = pages.flatMap((loaded) => loaded.pullRequests)
  const { fetchNextPage, isFetching } = query
  const needsMorePages =
    query.hasNextPage &&
    (Boolean(filter?.length) || (page + 1) * pageSize >= rows.length)
  useEffect(() => {
    if (needsMorePages && !isFetching) void fetchNextPage()
  }, [needsMorePages, isFetching, fetchNextPage])
  const matchingRows = rows
    .filter(
      (pr) =>
        queryClient.getQueryData([
          "my-pr-details",
          login,
          pr.repo,
          pr.number,
        ]) !== null
    )
    .filter((pr) =>
      `${pr.repo} #${pr.number} ${pr.title}`
        .toLowerCase()
        .includes(search.toLowerCase())
    )
  const requestedRows = filter?.length
    ? matchingRows
    : matchingRows.slice(page * pageSize, (page + 1) * pageSize)
  const detailQueries = useQueries({
    queries: requestedRows.map((pr) => ({
      queryKey: ["my-pr-details", login, pr.repo, pr.number],
      queryFn: () => api.myPullRequestDetails(pr.repo, pr.number),
      enabled: pr.detailsLoading === true,
      staleTime: Infinity,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      retry: false,
    })),
  })
  const all = matchingRows.flatMap((pr) => {
    const index = requestedRows.findIndex(
      (row) => row.repo === pr.repo && row.number === pr.number
    )
    const detail = detailQueries[index]
    if (detail?.data === null) return []
    if (detail?.data)
      return [
        {
          ...pr,
          ...detail.data,
          detailsLoading: false,
          detailsError: false,
          title: detail.data.title || pr.title,
          createdAt: detail.data.createdAt || pr.createdAt,
          updatedAt: detail.data.updatedAt || pr.updatedAt,
        },
      ]
    return [
      {
        ...pr,
        detailsLoading: pr.detailsLoading && !detail?.isError,
        detailsError: detail?.isError,
      },
    ]
  })
  const filtered = all.filter(
    (pr) =>
      !filter?.length ||
      filter.some((status) => statusLabels(pr).includes(status))
  )
  const visible = filtered.slice(page * pageSize, (page + 1) * pageSize)
  const selected = all.filter((pr) => selection.has(pullRequestKey(pr)))
  const selectedOnPage = visible.filter((pr) =>
    selection.has(pullRequestKey(pr))
  ).length
  const toggleSelection = (keys: string[], include: boolean) =>
    setSelection((current) => {
      const next = new Set(current)
      for (const key of keys) {
        if (include) next.add(key)
        else next.delete(key)
      }
      return next
    })
  const detailsLoading = detailQueries.some((detail) => detail.isFetching)
  const refreshing = query.isFetching && !query.isFetchingNextPage
  const incomplete = pages.some((loaded) => loaded.incomplete)
  const reviewRefs = visible.map((pr) => ({ repo: pr.repo, number: pr.number }))
  const reviews = useQuery({
    queryKey: ["my-pr-review-summaries", login, reviewRefs],
    queryFn: () => api.reviewSummaries(reviewRefs),
    enabled: reviewRefs.length > 0,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  })
  const accessibleRepos = (knownRepos.data?.repositories ?? []).map(
    (known) => known.full_name
  )
  // A timed-out search returns no PRs, and filtering by repository is the way
  // out of it, so the options cannot be derived from the PRs themselves.
  const repoNames = [
    ...new Set([
      ...(accessibleRepos.length ? accessibleRepos : all.map((pr) => pr.repo)),
      ...repo,
    ]),
  ].sort()

  return (
    <section
      className="mt-5 max-w-4xl space-y-4"
      aria-label="My open pull requests"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
        <span aria-live="polite">
          {refreshing
            ? "Refreshing from GitHub…"
            : latest
              ? `Refreshed ${dateLabel(latest.updatedAt)}`
              : "Live open pull requests from GitHub"}
        </span>
        <Button
          size="sm"
          variant="outline"
          disabled={query.isFetching}
          onClick={() => {
            queryClient.removeQueries({
              queryKey: ["my-pr-details", login],
              predicate: (cached) => cached.state.data === null,
            })
            void query.refetch()
            void queryClient.invalidateQueries({
              queryKey: ["my-pr-details", login],
            })
            void queryClient.invalidateQueries({
              queryKey: ["pr-thread-status", login],
            })
            if (reviewRefs.length) void reviews.refetch()
          }}
        >
          Refresh
        </Button>
      </div>
      <div className="flex flex-wrap gap-2">
        <MultiSelect
          label="Filter by repository"
          placeholder="All repositories"
          searchPlaceholder="Search repositories…"
          emptyMessage={
            knownRepos.isPending
              ? "Loading repositories…"
              : knownRepos.isError
                ? "Could not load repositories"
                : "No matches"
          }
          options={repoNames}
          value={repo}
          onValueChange={(chosen) =>
            onFiltersChange({ repo: chosen.length ? chosen : undefined })
          }
        />
        <input
          className={cn(control, "min-w-40 flex-1")}
          aria-label="Search pull requests"
          placeholder="Search title or PR number…"
          value={search}
          onChange={(event) =>
            onFiltersChange({ q: event.target.value || undefined }, true)
          }
        />
        <MultiSelect
          label="Filter by status"
          placeholder="All statuses"
          options={reviewStatuses}
          value={filter ?? []}
          onValueChange={(status) =>
            onFiltersChange({ status: status.length ? status : undefined })
          }
        />
      </div>
      {query.error && (
        <p role="alert" className="text-sm text-destructive">
          {latest &&
            (query.isFetchNextPageError
              ? "Could not load more PRs; showing the pages loaded so far. "
              : "Refresh failed; showing the previous snapshot. ")}
          {query.error.message}
        </p>
      )}
      {query.isLoading ? (
        <Skeleton className="h-56 w-full" />
      ) : incomplete ? (
        <p
          role="status"
          className="rounded-lg border border-border bg-card px-4 py-12 text-center text-xs text-amber-700 dark:text-amber-400"
        >
          GitHub&rsquo;s pull request search timed out, and the partial answer
          it returned would have hidden most of your PRs. Filter by repository
          to narrow the search, or refresh to try again.
        </p>
      ) : (
        latest && (
          <>
            {selected.length > 0 && (
              <BulkActions
                selected={selected}
                login={login}
                onClear={() => setSelection(new Set())}
                onSettled={(succeeded) =>
                  toggleSelection(succeeded.map(pullRequestKey), false)
                }
              />
            )}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  aria-label="Select all PRs on this page"
                  checked={
                    visible.length > 0 && selectedOnPage === visible.length
                  }
                  ref={(node) => {
                    if (node)
                      node.indeterminate =
                        selectedOnPage > 0 && selectedOnPage < visible.length
                  }}
                  onChange={(event) =>
                    toggleSelection(
                      visible.map(pullRequestKey),
                      event.target.checked
                    )
                  }
                />
                Select all on this page
              </label>
              <span className="ml-auto flex items-center gap-1">
                Sort
                {(
                  [
                    ["Last updated", "updatedAt"],
                    ["Created", "createdAt"],
                  ] as const
                ).map(([label, key]) => (
                  <button
                    key={key}
                    type="button"
                    aria-pressed={sort === key}
                    onClick={() => toggleSort(key)}
                    className={cn(
                      "inline-flex items-center gap-1 rounded-md border px-2 py-1",
                      sort === key
                        ? "border-border bg-muted text-foreground"
                        : "border-transparent hover:text-foreground"
                    )}
                  >
                    {label}
                    <span aria-hidden="true">
                      {sort === key ? (direction === "asc" ? "↑" : "↓") : "↕"}
                    </span>
                  </button>
                ))}
              </span>
            </div>
            <ul className="space-y-3">
              {visible.map((pr) => (
                <PullRequestCard
                  key={`${pr.repo}#${pr.number}`}
                  pr={pr}
                  login={login}
                  selected={selection.has(pullRequestKey(pr))}
                  onSelect={(include) =>
                    toggleSelection([pullRequestKey(pr)], include)
                  }
                  review={
                    reviews.isError ? null : reviews.data?.[
                        `${pr.repo}#${pr.number}`.toLowerCase()
                      ] ? (
                      <ReviewIndicators
                        review={
                          reviews.data[`${pr.repo}#${pr.number}`.toLowerCase()]!
                        }
                      />
                    ) : reviews.isPending ? (
                      <span>Loading review…</span>
                    ) : !Object.hasOwn(
                        reviews.data ?? {},
                        `${pr.repo}#${pr.number}`.toLowerCase()
                      ) ? (
                      <span>Review unavailable</span>
                    ) : (
                      <span>Not reviewed</span>
                    )
                  }
                  onRemoved={() => {
                    forgetPullRequest(queryClient, login, pr)
                    if (visible.length === 1 && page > 0)
                      onFiltersChange({ page: page - 1 || undefined }, true)
                  }}
                  onReady={() => refreshPullRequest(queryClient, login, pr)}
                />
              ))}
              {visible.length === 0 && (
                <li className="rounded-lg border border-border bg-card px-4 py-12 text-center text-xs text-muted-foreground">
                  {detailsLoading || query.isFetchingNextPage
                    ? "Loading matching PRs…"
                    : all.length
                      ? "No PRs match these filters."
                      : "No open PRs found."}
                </li>
              )}
            </ul>
            <div className="flex items-center gap-3 text-xs">
              <Button
                size="sm"
                variant="outline"
                disabled={page === 0 || refreshing}
                onClick={() => onFiltersChange({ page: page - 1 || undefined })}
              >
                Prev
              </Button>
              <span>Page {page + 1}</span>
              <Button
                size="sm"
                variant="outline"
                disabled={
                  ((page + 1) * pageSize >= filtered.length &&
                    !query.hasNextPage) ||
                  query.isFetching
                }
                onClick={() => onFiltersChange({ page: page + 1 })}
              >
                Next
              </Button>
              {query.isFetchingNextPage ? (
                <span role="status">Loading more PRs from GitHub…</span>
              ) : (
                detailsLoading && <span role="status">Loading PR details…</span>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              {visible.length} of {filtered.length}
              {query.hasNextPage ? "+" : ""} PRs · Added/deleted lines include
              tests and docs. PRs with checks still running are Pending.
            </p>
          </>
        )
      )}
    </section>
  )
}
