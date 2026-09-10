import { Link } from "@tanstack/react-router"
import { useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  BugBeetleIcon,
  CaretDownIcon,
  CheckIcon,
  FlagIcon,
  GitPullRequestIcon,
  PlusIcon,
  XIcon,
} from "@phosphor-icons/react"

import type {
  ReviewQueueChecksMode,
  ReviewQueueItem,
  ReviewQueueRepo,
  ReviewQueueReposPayload,
} from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { Popover, PopoverPopup, PopoverTrigger } from "@/components/ui/popover"
import { Button, buttonVariants } from "@/components/ui/button"
import { api } from "@/lib/api"
import { useRepos } from "@/lib/profile"
import { useSession } from "@/lib/session"
import { cn, formatRelativeTime } from "@/lib/utils"

const FULL_NAME_RE = /^[^\s/]+\/[^\s/]+$/
const MAX_SUGGESTIONS = 50

/** Keyed by login so one account's queue never bleeds into another in one SPA session. */
const reposQueryKey = (login: string | null) =>
  ["reviewQueueRepos", login] as const
const queueQueryKey = (login: string | null) => ["reviewQueue", login] as const
// Mirrors the `first: 100` on the backend's GitHub search.
const QUEUE_SEARCH_LIMIT = 100

const DECISION_LABELS: Record<string, string> = {
  APPROVED: "Approved",
  CHANGES_REQUESTED: "Changes requested",
  REVIEW_REQUIRED: "Review required",
}

function DecisionBadge({ decision }: { decision: string | null }) {
  if (!decision) return null
  const label = DECISION_LABELS[decision] ?? decision
  return (
    <Badge
      variant={decision === "CHANGES_REQUESTED" ? "destructive" : "outline"}
      className={cn(
        decision === "APPROVED" && "text-emerald-600 dark:text-emerald-400"
      )}
    >
      {label}
    </Badge>
  )
}

function AiReviewIndicator({ item }: { item: ReviewQueueItem }) {
  const review = item.ai_review
  if (!review) {
    return <span className="text-xs text-muted-foreground">No AI review</span>
  }
  return (
    <span className="flex items-center gap-3 text-xs">
      {review.status === "running" && (
        <span className="inline-flex items-center gap-1.5 text-muted-foreground">
          <span className="size-1.5 animate-pulse rounded-full bg-amber-500" />
          Reviewing
        </span>
      )}
      <span
        className={cn(
          "inline-flex items-center gap-1",
          review.counts.bugs > 0 ? "text-destructive" : "text-muted-foreground"
        )}
      >
        <BugBeetleIcon className="size-3.5" />
        {review.counts.bugs}
      </span>
      <span className="inline-flex items-center gap-1 text-muted-foreground">
        <FlagIcon className="size-3.5" />
        {review.counts.flags}
      </span>
    </span>
  )
}

function ReviewQueueRepoPicker({
  repos,
  saving,
  onChange,
}: {
  repos: Array<ReviewQueueRepo>
  saving: boolean
  onChange: (next: Array<ReviewQueueRepo>) => void
}) {
  const [text, setText] = useState("")
  const installed = useRepos()

  const query = text.trim().toLowerCase()
  const suggestions = useMemo(() => {
    const all = (installed.data?.repositories ?? []).map((r) => r.full_name)
    const filtered = query
      ? all.filter((name) => name.toLowerCase().includes(query))
      : all
    return filtered.sort((a, b) => a.localeCompare(b)).slice(0, MAX_SUGGESTIONS)
  }, [installed.data?.repositories, query])

  const typed = text.trim()
  const selected = new Set(repos.map((r) => r.full_name))
  const canAddTyped = FULL_NAME_RE.test(typed) && !selected.has(typed)

  const toggle = (full_name: string) => {
    onChange(
      selected.has(full_name)
        ? repos.filter((r) => r.full_name !== full_name)
        : [...repos, { full_name, paths: [], checks: "required" }]
    )
  }

  return (
    <Popover>
      <PopoverTrigger
        className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
        data-testid="review-queue-repo-picker"
      >
        Repositories
        {repos.length > 0 && (
          <span className="text-muted-foreground">({repos.length})</span>
        )}
        <CaretDownIcon />
      </PopoverTrigger>
      <PopoverPopup align="start" className="w-72 max-w-none" side="bottom">
        <Input
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== "Enter" || !canAddTyped || saving) return
            event.preventDefault()
            toggle(typed)
            setText("")
          }}
          placeholder="owner/repo"
          aria-label="Add repository"
          data-testid="review-queue-repo-input"
          disabled={saving}
        />
        <div className="mt-2 max-h-64 overflow-y-auto">
          {canAddTyped && (
            <button
              type="button"
              onClick={() => {
                toggle(typed)
                setText("")
              }}
              data-testid="review-queue-repo-add"
              disabled={saving}
              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-foreground hover:bg-sidebar-row-hover disabled:opacity-50"
            >
              <PlusIcon className="size-3.5 shrink-0 text-muted-foreground" />
              <span className="truncate">Add {typed}</span>
            </button>
          )}
          {suggestions.map((full_name) => (
            <button
              key={full_name}
              type="button"
              onClick={() => toggle(full_name)}
              data-testid={`review-queue-repo-option-${full_name}`}
              disabled={saving}
              className="flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-xs text-foreground hover:bg-sidebar-row-hover disabled:opacity-50"
            >
              <span className="truncate">{full_name}</span>
              {selected.has(full_name) && (
                <CheckIcon className="size-3.5 shrink-0 text-foreground" />
              )}
            </button>
          ))}
          {suggestions.length === 0 && !canAddTyped && (
            <p className="px-2 py-1.5 text-xs text-muted-foreground">
              {installed.isLoading
                ? "Loading repositories…"
                : "Type owner/repo to add one."}
            </p>
          )}
        </div>
      </PopoverPopup>
    </Popover>
  )
}

const CHECKS_OPTIONS: Array<{
  value: ReviewQueueChecksMode
  label: string
}> = [
  { value: "required", label: "Required checks passed" },
  { value: "all", label: "All checks passed" },
  { value: "ignore", label: "Ignore checks" },
]

function chipLabel(repo: ReviewQueueRepo) {
  const parts: Array<string> = []
  if (repo.paths.length > 0) {
    parts.push(
      repo.paths.length <= 2
        ? repo.paths.join(", ")
        : `${repo.paths.length} paths`
    )
  }
  if (repo.checks !== "required") {
    parts.push(repo.checks === "all" ? "all checks" : "ignores checks")
  }
  if (parts.length === 0) return repo.full_name
  return `${repo.full_name} · ${parts.join(" · ")}`
}

function ReviewQueueRepoChip({
  repo,
  repos,
  saving,
}: {
  repo: ReviewQueueRepo
  repos: Array<ReviewQueueRepo>
  saving: boolean
}) {
  const qc = useQueryClient()
  const login = useSession().data?.login ?? null
  const [open, setOpen] = useState(false)
  const [text, setText] = useState("")
  const [checks, setChecks] = useState<ReviewQueueChecksMode>(repo.checks)

  const savePaths = useMutation({
    mutationFn: (paths: Array<string>) =>
      api.setReviewQueueRepos(
        repos.map((r) =>
          r.full_name === repo.full_name ? { ...r, paths, checks } : r
        )
      ),
    onSuccess: (payload: ReviewQueueReposPayload) => {
      qc.setQueryData(reposQueryKey(login), payload)
      void qc.invalidateQueries({ queryKey: queueQueryKey(login) })
      setOpen(false)
    },
  })

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (next) {
          setText(repo.paths.join("\n"))
          setChecks(repo.checks)
          savePaths.reset()
        }
      }}
    >
      <PopoverTrigger
        className="max-w-72 truncate text-xs text-foreground"
        data-testid={`review-queue-chip-${repo.full_name}`}
        disabled={saving}
        title={repo.paths.join("\n") || undefined}
      >
        {chipLabel(repo)}
      </PopoverTrigger>
      <PopoverPopup align="start" className="w-72 max-w-none" side="bottom">
        <p className="text-xs font-medium text-foreground">Checks</p>
        <div className="mt-2 space-y-1">
          {CHECKS_OPTIONS.map((option) => (
            <label
              key={option.value}
              className="flex items-center gap-2 text-xs text-foreground"
            >
              <input
                type="radio"
                name={`review-queue-checks-${repo.full_name}`}
                value={option.value}
                checked={checks === option.value}
                onChange={() => setChecks(option.value)}
                data-testid={`review-queue-checks-${option.value}`}
                disabled={savePaths.isPending}
                className="size-3.5 accent-foreground"
              />
              {option.label}
            </label>
          ))}
        </div>
        <p className="mt-3 text-xs font-medium text-foreground">
          Only PRs touching these paths
        </p>
        <Textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={"ui/\nagent/dashboard/"}
          aria-label={`Paths for ${repo.full_name}`}
          data-testid="review-queue-paths-input"
          disabled={savePaths.isPending}
          className="mt-2 font-mono"
        />
        <p className="mt-1 text-xs text-muted-foreground">
          One path prefix per line. Leave empty to include every PR.
        </p>
        {savePaths.error && (
          <p className="mt-1 text-xs text-destructive">
            {savePaths.error.message}
          </p>
        )}
        <div className="mt-2 flex items-center justify-end gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => setOpen(false)}
            disabled={savePaths.isPending}
          >
            Cancel
          </Button>
          <Button
            size="sm"
            data-testid="review-queue-paths-save"
            disabled={savePaths.isPending}
            onClick={() =>
              savePaths.mutate(
                text
                  .split("\n")
                  .map((line) => line.trim())
                  .filter(Boolean)
              )
            }
          >
            Save
          </Button>
        </div>
      </PopoverPopup>
    </Popover>
  )
}

export function ReviewQueue() {
  const session = useSession()
  const login = session.data?.login ?? null
  const qc = useQueryClient()

  const reposQuery = useQuery({
    queryKey: reposQueryKey(login),
    queryFn: api.getReviewQueueRepos,
    enabled: !!session.data,
  })
  const repos = reposQuery.data?.repos ?? []

  const save = useMutation({
    mutationFn: (next: Array<ReviewQueueRepo>) => api.setReviewQueueRepos(next),
    onSuccess: (payload: ReviewQueueReposPayload) => {
      qc.setQueryData(reposQueryKey(login), payload)
      void qc.invalidateQueries({ queryKey: queueQueryKey(login) })
    },
  })

  const queue = useQuery({
    queryKey: queueQueryKey(login),
    queryFn: api.getReviewQueue,
    enabled: !!session.data && repos.length > 0,
    refetchInterval: 60_000,
  })

  const queueData = repos.length > 0 ? queue.data : undefined
  const items = queueData?.items ?? []
  const totalOpen = queueData?.total_open ?? 0

  return (
    <div className="mt-3 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <ReviewQueueRepoPicker
          repos={repos}
          saving={save.isPending}
          onChange={(next) => save.mutate(next)}
        />
        {repos.map((repo) => (
          <span
            key={repo.full_name}
            className="inline-flex items-center gap-1 rounded-full border border-border bg-card py-0.5 pr-1 pl-2 text-xs text-foreground"
          >
            <ReviewQueueRepoChip
              repo={repo}
              repos={repos}
              saving={save.isPending}
            />
            <button
              type="button"
              aria-label={`Remove ${repo.full_name}`}
              disabled={save.isPending}
              onClick={() =>
                save.mutate(repos.filter((r) => r.full_name !== repo.full_name))
              }
              className="inline-flex size-4 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-sidebar-row-hover hover:text-foreground disabled:opacity-50"
            >
              <XIcon className="size-3" />
            </button>
          </span>
        ))}
      </div>

      {save.error && (
        <p className="text-xs text-destructive">{save.error.message}</p>
      )}

      <div className="overflow-hidden rounded-lg border border-border bg-card">
        {reposQuery.isLoading && (
          <div className="p-4">
            <Skeleton className="h-24 w-full" />
          </div>
        )}
        {reposQuery.error && (
          <p className="px-4 py-3 text-xs text-destructive">
            {reposQuery.error.message}
          </p>
        )}
        {reposQuery.data && repos.length === 0 && (
          <p
            data-testid="review-queue-empty"
            className="px-4 py-3 text-xs text-muted-foreground"
          >
            Pick repositories above to see pull requests that are ready for your
            review.
          </p>
        )}
        {repos.length > 0 && queue.isPending && (
          <div className="p-4">
            <Skeleton className="h-24 w-full" />
          </div>
        )}
        {repos.length > 0 && queue.isError && (
          <p
            data-testid="review-queue-error"
            className="px-4 py-3 text-xs text-destructive"
          >
            {queue.error.message || "Could not load pull requests."}
          </p>
        )}
        {queueData && items.length === 0 && (
          <p
            data-testid="review-queue-no-items"
            className="px-4 py-3 text-xs text-muted-foreground"
          >
            No pull requests ready for review in {repos.length} repositories.
          </p>
        )}
        <div className="divide-y divide-border">
          {items.map((item) => (
            <Link
              key={`${item.repo_full_name}#${item.number}`}
              to="/$owner/$repo/pull/$number"
              params={{
                owner: item.owner,
                repo: item.repo,
                number: String(item.number),
              }}
              data-testid={`review-queue-row-${item.repo_full_name}-${item.number}`}
              className="flex items-center justify-between gap-4 px-4 py-3 transition-colors hover:bg-sidebar-row-hover"
            >
              <div className="flex min-w-0 items-center gap-3">
                <GitPullRequestIcon className="size-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0">
                  <div className="truncate text-xs font-medium text-foreground">
                    {item.title}
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
                    <span>
                      {item.repo_full_name}#{item.number}
                    </span>
                    {item.author && (
                      <span>
                        by{" "}
                        <span className="font-semibold text-foreground">
                          {item.author}
                        </span>
                      </span>
                    )}
                    <span className="tabular-nums">
                      <span className="text-emerald-600 dark:text-emerald-400">
                        +{item.additions}
                      </span>{" "}
                      <span className="text-destructive">
                        −{item.deletions}
                      </span>
                    </span>
                    <span>
                      {item.changed_files}{" "}
                      {item.changed_files === 1 ? "file" : "files"}
                    </span>
                    {item.matched_paths.map((path) => (
                      <span
                        key={path}
                        data-testid="review-queue-matched-path"
                        className="rounded bg-muted px-1 font-mono text-[11px]"
                      >
                        {path}
                      </span>
                    ))}
                    {item.optional_failures > 0 && (
                      <span
                        data-testid="review-queue-optional-failures"
                        title="Non-required checks failing on this PR"
                        className="rounded bg-muted px-1 font-mono text-[11px] text-amber-600 dark:text-amber-400"
                      >
                        {item.optional_failures} optional{" "}
                        {item.optional_failures === 1 ? "check" : "checks"}{" "}
                        failing
                      </span>
                    )}
                  </div>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-3">
                <DecisionBadge decision={item.review_decision} />
                <AiReviewIndicator item={item} />
                <span className="text-xs text-muted-foreground">
                  {formatRelativeTime(new Date(item.updated_at).getTime())}
                </span>
              </div>
            </Link>
          ))}
        </div>
        {totalOpen > QUEUE_SEARCH_LIMIT && (
          <p
            data-testid="review-queue-truncated"
            className="border-t border-border px-4 py-2 text-xs text-muted-foreground"
          >
            Showing the {QUEUE_SEARCH_LIMIT} most recently updated of{" "}
            {totalOpen} open pull requests.
          </p>
        )}
      </div>
    </div>
  )
}
