import { IoLogoGithub } from "react-icons/io5"

import { DiffStat } from "@/components/DiffStat"
import { PrStateBadge, toPrState } from "@/components/PrState"
import { cn } from "@/lib/utils"

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
  compact?: boolean
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
  compact = false,
}: PrHeaderProps) {
  return (
    <div className={className}>
      <div className="flex min-w-0 items-center gap-2">
        <PrStateBadge icon state={toPrState(state)} />
        <h1
          className={cn(
            compact
              ? "min-w-0 flex-1 truncate text-sm font-medium"
              : "min-w-0 text-base font-medium",
            titleClassName
          )}
        >
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            className={cn("hover:underline", compact && "block truncate")}
          >
            <IoLogoGithub
              aria-label="GitHub"
              className="mr-1.5 inline size-4 align-[-2px] text-muted-foreground"
            />
            {title}
            {number != null && (
              <span className="text-muted-foreground"> #{number}</span>
            )}
          </a>
        </h1>
      </div>
      <div
        className={cn(
          "flex items-center gap-2 text-xs text-muted-foreground",
          compact ? "mt-1.5 min-w-0 overflow-hidden" : "mt-2 flex-wrap"
        )}
      >
        {author && (
          <span className="shrink-0 font-medium text-foreground">{author}</span>
        )}
        {compact ? (
          <>
            <span className="min-w-0 truncate font-mono" title={headRef}>
              {headRef}
            </span>
            <span className="shrink-0">→</span>
            <span className="min-w-0 truncate font-mono" title={baseRef}>
              {baseRef}
            </span>
          </>
        ) : (
          <>
            <span className="rounded border border-border px-1.5 py-0.5 font-mono text-[11px]">
              {baseRef}
            </span>
            <span>←</span>
            <span className="rounded border border-border px-1.5 py-0.5 font-mono text-[11px]">
              {headRef}
            </span>
          </>
        )}
        {stats && (
          <>
            <span className="shrink-0">
              {stats.changedFiles} file{stats.changedFiles === 1 ? "" : "s"}
            </span>
            <DiffStat
              additions={stats.additions}
              className="shrink-0"
              deletions={stats.deletions}
            />
          </>
        )}
      </div>
    </div>
  )
}
