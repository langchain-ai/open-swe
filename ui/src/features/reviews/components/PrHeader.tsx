import { GitPullRequestIcon } from "@phosphor-icons/react/dist/ssr/GitPullRequest"
import { GithubLogoIcon } from "@phosphor-icons/react/dist/ssr/GithubLogo"

import { cn, formatRelativeTime } from "@/lib/utils"

const STATE_STYLES: Record<string, string> = {
  open: "border-status-green text-status-green",
  draft: "border-default text-secondary",
  merged: "border-purple text-purple",
  closed: "border-status-red text-status-red",
}

export interface PrHeaderProps {
  url: string
  title: string
  state: string
  headRef: string
  baseRef: string
  number?: number | null
  author?: string | null
  createdAt?: string | null
  mergedAt?: string | null
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
  createdAt,
  mergedAt,
  stats,
  className,
  titleClassName,
  compact = false,
}: PrHeaderProps) {
  return (
    <div className={className}>
      <div className="flex min-w-0 items-center gap-2">
        <span
          className={cn(
            "inline-flex shrink-0 items-center gap-space-1 rounded-full border px-space-2 py-0.5 text-xxs capitalize",
            STATE_STYLES[state] ?? STATE_STYLES.open
          )}
        >
          <GitPullRequestIcon weight="regular" className="size-3" />
          {state}
        </span>
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
            <GithubLogoIcon
              aria-label="GitHub"
              weight="regular"
              className="mr-1.5 inline size-4 align-[-2px] text-icon-secondary"
            />
            {title}
            {number != null && (
              <span className="text-secondary"> #{number}</span>
            )}
          </a>
        </h1>
      </div>
      <div
        className={cn(
          "flex items-center gap-2 text-xs text-secondary",
          compact ? "mt-1.5 min-w-0 overflow-hidden" : "mt-2 flex-wrap"
        )}
      >
        {author && (
          <span className="shrink-0 font-medium text-primary">{author}</span>
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
            <span className="rounded border border-default px-1.5 py-0.5 font-mono text-xxs">
              {baseRef}
            </span>
            <span>←</span>
            <span className="rounded border border-default px-1.5 py-0.5 font-mono text-xxs">
              {headRef}
            </span>
          </>
        )}
        {stats && (
          <>
            <span className="shrink-0">
              {stats.changedFiles} file{stats.changedFiles === 1 ? "" : "s"}
            </span>
            <span className="shrink-0 text-success-secondary">
              +{stats.additions}
            </span>
            <span className="shrink-0 text-error-secondary">
              -{stats.deletions}
            </span>
          </>
        )}
      </div>
      {(createdAt || mergedAt) && (
        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-xs text-secondary">
          {(
            [
              ["Opened", createdAt],
              ["Merged", mergedAt],
            ] as const
          ).map(
            ([label, value]) =>
              value && (
                <span key={label}>
                  {label}{" "}
                  <time
                    dateTime={value}
                    title={new Date(value).toLocaleString()}
                    suppressHydrationWarning
                  >
                    {formatRelativeTime(new Date(value).getTime())}
                  </time>
                </span>
              )
          )}
        </div>
      )}
    </div>
  )
}
