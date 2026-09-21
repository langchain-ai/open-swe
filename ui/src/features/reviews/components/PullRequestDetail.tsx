import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { XIcon } from "@phosphor-icons/react"

import type { PreviewFile, PullRequestPreview } from "@/lib/api"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

const statusMarks: Record<PreviewFile["status"], string> = {
  added: "A",
  removed: "D",
  modified: "M",
  renamed: "R",
  copied: "C",
  changed: "M",
  unchanged: "·",
}

const statusTones: Record<PreviewFile["status"], string> = {
  added: "text-emerald-700 dark:text-emerald-400",
  removed: "text-destructive",
  modified: "text-muted-foreground",
  renamed: "text-sky-700 dark:text-sky-400",
  copied: "text-sky-700 dark:text-sky-400",
  changed: "text-muted-foreground",
  unchanged: "text-muted-foreground",
}

function Section({
  heading,
  count,
  children,
}: {
  heading: string
  count?: string
  children: React.ReactNode
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
        className={cn("w-3 shrink-0", statusTones[file.status])}
        title={file.status}
      >
        {statusMarks[file.status]}
      </span>
      <span className="min-w-0 flex-1 truncate" title={file.path} dir="rtl">
        <span dir="ltr">
          <span className="text-muted-foreground">
            {cut < 0 ? "" : file.path.slice(0, cut + 1)}
          </span>
          <span className="text-foreground">{file.path.slice(cut + 1)}</span>
        </span>
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

function Blockers({ preview }: { preview: PullRequestPreview }) {
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

/**
 * Preview of one pull request beside the list: what blocks it, what it
 * touches, and why. Ordered for triage rather than for reading — the threads
 * that hold up a merge come before the description that explains it.
 */
export function PullRequestDetail({
  owner,
  repo,
  number,
  onClose,
}: {
  owner: string
  repo: string
  number: number
  onClose: () => void
}) {
  const preview = useQuery({
    queryKey: ["pr-preview", owner, repo, number],
    queryFn: () => api.getPullRequestPreview(owner, repo, number),
    staleTime: 60_000,
  })
  const data = preview.data

  return (
    <aside
      aria-label={`Pull request ${owner}/${repo} #${number}`}
      className="flex min-h-0 w-full flex-col overflow-hidden rounded-lg border border-border bg-card"
    >
      <header className="flex items-start gap-3 border-b border-border px-5 py-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2 text-xs text-muted-foreground">
            <span className="truncate">
              {owner}/{repo}
            </span>
            <span className="font-mono tabular-nums">#{number}</span>
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
            {data?.title ?? `Pull request #${number}`}
          </h2>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <Link
              className="text-muted-foreground hover:text-foreground hover:underline"
              to="/agents/reviews/$owner/$repo/$number"
              params={{ owner, repo, number: String(number) }}
            >
              Open full review
            </Link>
            <a
              className="text-muted-foreground hover:text-foreground hover:underline"
              href={`https://github.com/${owner}/${repo}/pull/${number}`}
              target="_blank"
              rel="noreferrer"
            >
              View on GitHub
            </a>
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
            <Section
              heading="Unresolved comments"
              count={
                data.unresolved === null
                  ? undefined
                  : String(data.unresolved.length)
              }
            >
              <Blockers preview={data} />
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
          </>
        )}
      </div>
    </aside>
  )
}
