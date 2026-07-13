import { GitPullRequestIcon } from "@phosphor-icons/react"

import { cn } from "@/lib/utils"

const STATE_STYLES: Record<string, string> = {
  open: "border-emerald-600/40 text-emerald-500",
  draft: "border-border text-muted-foreground",
  merged: "border-purple-600/40 text-purple-500",
  closed: "border-red-600/40 text-red-500",
}

export interface PrHeaderProps {
  url: string
  title: string
  state: string
  headRef: string
  baseRef: string
  number?: number | null
  author?: string | null
  stats?: {
    changedFiles: number
    additions: number
    deletions: number
  } | null
  className?: string
  titleClassName?: string
}

export function PrHeader({
  url,
  title,
  state,
  headRef,
  baseRef,
  number,
  author,
  stats,
  className,
  titleClassName,
}: PrHeaderProps) {
  return (
    <div className={className}>
      <span
        className={cn(
          "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] capitalize",
          STATE_STYLES[state] ?? STATE_STYLES.open
        )}
      >
        <GitPullRequestIcon className="size-3" />
        {state}
      </span>
      <h1 className={cn("mt-2 text-base font-medium", titleClassName)}>
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          className="hover:underline"
        >
          {title}
          {number != null && (
            <span className="text-muted-foreground"> #{number}</span>
          )}
        </a>
      </h1>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {author && (
          <span className="font-medium text-foreground">{author}</span>
        )}
        <span className="rounded border border-border px-1.5 py-0.5 font-mono text-[11px]">
          {baseRef}
        </span>
        <span>←</span>
        <span className="rounded border border-border px-1.5 py-0.5 font-mono text-[11px]">
          {headRef}
        </span>
        {stats && (
          <>
            <span>
              {stats.changedFiles} file{stats.changedFiles === 1 ? "" : "s"}
            </span>
            <span className="text-emerald-500">+{stats.additions}</span>
            <span className="text-red-500">-{stats.deletions}</span>
          </>
        )}
      </div>
    </div>
  )
}
