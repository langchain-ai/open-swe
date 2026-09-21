import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { XIcon } from "@phosphor-icons/react"
import type { ReactNode } from "react"

import type {
  OpenPullRequest,
  PreviewCheck,
  PreviewFile,
  PullRequestPreview,
} from "@/lib/api"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"
import {
  PullRequestActions,
  type PullRequestOutcome,
} from "./PullRequestActions"

const fileMarks: Record<string, string> = {
  added: "A",
  removed: "D",
  modified: "M",
  renamed: "R",
  copied: "C",
  changed: "M",
  unchanged: "·",
}

const fileTones: Record<string, string> = {
  added: "text-emerald-700 dark:text-emerald-400",
  removed: "text-destructive",
  renamed: "text-sky-700 dark:text-sky-400",
  copied: "text-sky-700 dark:text-sky-400",
}

const passedConclusions = new Set(["success", "neutral", "skipped"])

function checkTone(check: PreviewCheck): string {
  if (check.status !== "completed") return "text-amber-700 dark:text-amber-400"
  if (check.conclusion && passedConclusions.has(check.conclusion))
    return "text-emerald-700 dark:text-emerald-400"
  return "text-destructive"
}

function checkRank(check: PreviewCheck): number {
  if (check.status !== "completed") return 1
  if (check.conclusion && passedConclusions.has(check.conclusion)) return 2
  return 0
}

function Section({
  heading,
  count,
  children,
}: {
  heading: string
  count?: string
  children: ReactNode
}) {
  return (
    <section className="border-t border-border px-5 py-4 first:border-t-0">
      <div className="mb-2.5 flex items-baseline gap-2">
        <h3 className="text-xs font-medium text-foreground">{heading}</h3>
        {count && (
          <span className="text-xs text-muted-foreground tabular-nums">
            {count}
          </span>
        )}
      </div>
      {children}
    </section>
  )
}

function FileRow({ file }: { file: PreviewFile }) {
  const cut = file.path.lastIndexOf("/")
  return (
    <li className="flex items-baseline gap-2.5 py-1 font-mono text-xs">
      <span
        aria-hidden="true"
        className={cn("w-3 shrink-0", fileTones[file.status])}
        title={file.status}
      >
        {fileMarks[file.status] ?? "M"}
      </span>
      <span className="min-w-0 flex-1 truncate" title={file.path}>
        <span className="text-muted-foreground">
          {cut < 0 ? "" : file.path.slice(0, cut + 1)}
        </span>
        <span className="text-foreground">{file.path.slice(cut + 1)}</span>
      </span>
      <span className="shrink-0 text-emerald-700 tabular-nums dark:text-emerald-400">
        +{file.additions}
      </span>
      <span className="w-12 shrink-0 text-destructive tabular-nums">
        −{file.deletions}
      </span>
    </li>
  )
}

function CheckRow({ check }: { check: PreviewCheck }) {
  return (
    <li className="flex items-baseline gap-2.5 py-0.5 text-xs">
      <span className={cn("shrink-0 tabular-nums", checkTone(check))}>
        {check.status !== "completed"
          ? "•"
          : check.conclusion === "success"
            ? "✓"
            : "✕"}
      </span>
      <span className="min-w-0 flex-1 truncate text-foreground">
        {check.url ? (
          <a
            className="hover:underline"
            href={check.url}
            target="_blank"
            rel="noreferrer"
          >
            {check.name}
          </a>
        ) : (
          check.name
        )}
      </span>
      <span className={cn("shrink-0", checkTone(check))}>
        {check.status !== "completed"
          ? check.status
          : (check.conclusion ?? "done")}
      </span>
    </li>
  )
}

// A repo with hundreds of checks turns the preview into a wall of green, so
// past this many they collapse and only what needs attention stays open.
const groupChecksAbove = 20

const checkGroups = [
  ["Failing", 0],
  ["Running", 1],
  ["Passed", 2],
] as const

function Checks({ checks }: { checks: Array<PreviewCheck> | null }) {
  if (checks === null) {
    return (
      <p className="text-xs text-amber-700 dark:text-amber-400">
        GitHub did not return the checks for this commit.
      </p>
    )
  }
  if (checks.length === 0) {
    return <p className="text-xs text-muted-foreground">No checks ran.</p>
  }
  const sorted = [...checks].sort((a, b) => checkRank(a) - checkRank(b))
  if (sorted.length <= groupChecksAbove) {
    return (
      <ul className="space-y-0.5">
        {sorted.map((check) => (
          <CheckRow key={`${check.name}:${check.url ?? ""}`} check={check} />
        ))}
      </ul>
    )
  }
  return (
    <div className="space-y-1.5">
      {checkGroups.map(([label, rank]) => {
        const group = sorted.filter((check) => checkRank(check) === rank)
        if (!group.length) return null
        return (
          <details key={label} open={rank === 0} className="group">
            <summary className="cursor-pointer list-none text-xs text-muted-foreground hover:text-foreground">
              <span aria-hidden="true" className="inline-block w-3">
                {"›"}
              </span>
              {label}
              <span className="ml-1.5 tabular-nums">{group.length}</span>
            </summary>
            <ul className="mt-1 space-y-0.5 pl-3">
              {group.map((check) => (
                <CheckRow
                  key={`${check.name}:${check.url ?? ""}`}
                  check={check}
                />
              ))}
            </ul>
          </details>
        )
      })}
    </div>
  )
}

function Conversations({ preview }: { preview: PullRequestPreview }) {
  if (preview.unresolved === null) {
    return (
      <p className="text-xs text-amber-700 dark:text-amber-400">
        GitHub did not return the review threads, so unresolved comments cannot
        be counted here. Open the PR to check.
      </p>
    )
  }
  if (preview.unresolved.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        Nothing unresolved. Every review thread on this PR is closed.
      </p>
    )
  }
  return (
    <ul className="space-y-2.5">
      {preview.unresolved.map((thread, index) => (
        <li
          key={thread.url ?? `${thread.path}:${thread.line}:${index}`}
          className="border-l-2 border-amber-600/40 pl-3"
        >
          <div className="flex items-baseline gap-2 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">
              {thread.author ?? "Someone"}
            </span>
            <span className="min-w-0 truncate font-mono">
              {thread.path}
              {thread.line !== null && `:${thread.line}`}
            </span>
            {thread.url && (
              <a
                className="ml-auto shrink-0 hover:text-foreground hover:underline"
                href={thread.url}
                target="_blank"
                rel="noreferrer"
              >
                Reply
              </a>
            )}
          </div>
          <p className="mt-1 line-clamp-3 text-xs whitespace-pre-wrap text-foreground">
            {thread.body}
          </p>
        </li>
      ))}
    </ul>
  )
}

/** One pull request beside the list, in the shape of GitHub's own summary. */
export function PullRequestDetail({
  pr,
  login,
  outcome,
  onClose,
  onSettled,
  onReady,
}: {
  pr: OpenPullRequest
  login: string
  outcome?: PullRequestOutcome
  onClose: () => void
  onSettled: (outcome: PullRequestOutcome) => void
  onReady: () => void
}) {
  const [owner, name] = pr.repo.split("/")
  const preview = useQuery({
    queryKey: ["pr-preview", owner, name, pr.number],
    queryFn: () => api.getPullRequestPreview(owner!, name!, pr.number),
    staleTime: 60_000,
  })
  const data = preview.data
  const failing =
    data?.checks?.filter((check) => checkRank(check) === 0).length ?? 0
  const running =
    data?.checks?.filter((check) => checkRank(check) === 1).length ?? 0

  return (
    <aside
      aria-label={`Pull request ${pr.repo} #${pr.number}`}
      className="flex min-h-0 w-full flex-col overflow-hidden rounded-lg border border-border bg-card"
    >
      <header className="flex items-start gap-3 border-b border-border px-5 py-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2 text-xs text-muted-foreground">
            <span className="truncate">{pr.repo}</span>
            <span className="font-mono tabular-nums">#{pr.number}</span>
            {data && (
              <span className="tabular-nums">
                <span className="text-emerald-700 dark:text-emerald-400">
                  +{data.additions}
                </span>{" "}
                <span className="text-destructive">−{data.deletions}</span>
              </span>
            )}
          </div>
          <h2 className="mt-1 text-sm font-medium break-words text-foreground">
            {data?.title ?? pr.title}
          </h2>
          {data && (
            <p className="mt-1 text-xs text-muted-foreground">
              {data.author ?? "Someone"} wants to merge {data.commits}{" "}
              {data.commits === 1 ? "commit" : "commits"} into{" "}
              <span className="font-mono">{data.base_ref}</span> from{" "}
              <span className="font-mono">{data.head_ref}</span>
            </p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <Link
              className="text-muted-foreground hover:text-foreground hover:underline"
              to="/agents/reviews/$owner/$repo/$number"
              params={{
                owner: owner!,
                repo: name!,
                number: String(pr.number),
              }}
            >
              Open full review
            </Link>
          </div>
        </div>
        <button
          type="button"
          aria-label="Close pull request preview"
          onClick={onClose}
          className="-mt-1 -mr-1.5 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-sidebar-row-hover hover:text-foreground"
        >
          <XIcon className="size-4" />
        </button>
      </header>

      <div className="border-b border-border px-5 py-3">
        <PullRequestActions
          pr={pr}
          login={login}
          outcome={outcome}
          onSettled={onSettled}
          onReady={onReady}
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {preview.isPending && (
          <div className="space-y-3 p-5">
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        )}
        {preview.error && (
          <p role="alert" className="px-5 py-4 text-xs text-destructive">
            {preview.error.message}
          </p>
        )}
        {data && (
          <>
            <Section heading="Description">
              {data.body ? (
                <div className="max-w-[72ch]">
                  <Markdown content={data.body} />
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">
                  This PR has no description.
                </p>
              )}
            </Section>

            <Section
              heading="Unresolved comments"
              count={
                data.unresolved === null
                  ? undefined
                  : String(data.unresolved.length)
              }
            >
              <Conversations preview={data} />
            </Section>

            <Section
              heading="Changed files"
              count={
                data.changed_files > data.files.length
                  ? `${data.files.length} of ${data.changed_files}`
                  : String(data.changed_files)
              }
            >
              {data.files.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No files changed.
                </p>
              ) : (
                <ul>
                  {data.files.map((file) => (
                    <FileRow key={file.path} file={file} />
                  ))}
                </ul>
              )}
            </Section>

            <Section
              heading="Checks"
              count={
                data.checks === null
                  ? undefined
                  : failing || running
                    ? `${failing} failing, ${running} running, ${data.checks.length} total`
                    : String(data.checks.length)
              }
            >
              <Checks checks={data.checks} />
            </Section>
          </>
        )}
      </div>
    </aside>
  )
}
