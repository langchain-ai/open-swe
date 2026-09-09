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

import type { ReviewQueueItem, ReviewQueueReposPayload } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Popover, PopoverPopup, PopoverTrigger } from "@/components/ui/popover"
import { buttonVariants } from "@/components/ui/button"
import { api } from "@/lib/api"
import { useRepos } from "@/lib/profile"
import { useSession } from "@/lib/session"
import { cn, formatRelativeTime } from "@/lib/utils"

const FULL_NAME_RE = /^[^\s/]+\/[^\s/]+$/
const MAX_SUGGESTIONS = 50

const REPOS_QUERY_KEY = ["reviewQueueRepos"] as const
const QUEUE_QUERY_KEY = ["reviewQueue"] as const

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
  repos: Array<string>
  saving: boolean
  onChange: (next: Array<string>) => void
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
  const selected = new Set(repos)
  const canAddTyped = FULL_NAME_RE.test(typed) && !selected.has(typed)

  const toggle = (full_name: string) => {
    onChange(
      selected.has(full_name)
        ? repos.filter((r) => r !== full_name)
        : [...repos, full_name]
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
            if (event.key !== "Enter" || !canAddTyped) return
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
              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-foreground hover:bg-sidebar-row-hover"
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
              className="flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-xs text-foreground hover:bg-sidebar-row-hover"
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

export function ReviewQueue() {
  const session = useSession()
  const qc = useQueryClient()

  const reposQuery = useQuery({
    queryKey: REPOS_QUERY_KEY,
    queryFn: api.getReviewQueueRepos,
    enabled: !!session.data,
  })
  const repos = reposQuery.data?.repos ?? []

  const save = useMutation({
    mutationFn: (next: Array<string>) => api.setReviewQueueRepos(next),
    onSuccess: (payload: ReviewQueueReposPayload) => {
      qc.setQueryData(REPOS_QUERY_KEY, payload)
      void qc.invalidateQueries({ queryKey: QUEUE_QUERY_KEY })
    },
  })

  const queue = useQuery({
    queryKey: QUEUE_QUERY_KEY,
    queryFn: api.getReviewQueue,
    enabled: !!session.data && repos.length > 0,
    refetchInterval: 60_000,
  })

  const items = queue.data?.items ?? []

  return (
    <div className="mt-3 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <ReviewQueueRepoPicker
          repos={repos}
          saving={save.isPending}
          onChange={(next) => save.mutate(next)}
        />
        {repos.map((full_name) => (
          <span
            key={full_name}
            data-testid={`review-queue-chip-${full_name}`}
            className="inline-flex items-center gap-1 rounded-full border border-border bg-card py-0.5 pr-1 pl-2 text-xs text-foreground"
          >
            {full_name}
            <button
              type="button"
              aria-label={`Remove ${full_name}`}
              disabled={save.isPending}
              onClick={() => save.mutate(repos.filter((r) => r !== full_name))}
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
        {repos.length > 0 && queue.isLoading && (
          <div className="p-4">
            <Skeleton className="h-24 w-full" />
          </div>
        )}
        {repos.length > 0 && queue.error && (
          <p className="px-4 py-3 text-xs text-destructive">
            {queue.error.message}
          </p>
        )}
        {repos.length > 0 && queue.data && items.length === 0 && (
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
                    {item.author && <span>by {item.author}</span>}
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
      </div>
    </div>
  )
}
