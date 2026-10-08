import { useQuery } from "@tanstack/react-query"
import {
  ArrowSquareOutIcon,
  CaretDownIcon,
  ChatCircleIcon,
  CheckIcon,
  CopyIcon,
} from "@phosphor-icons/react"
import { toast } from "sonner"

import { cn } from "@/lib/utils"
import { loadReviewFileContents } from "@/features/reviews/lib/fileContents"
import { AgentMark } from "./AgentMark"
import { isRenderable } from "./diffEntries"
import { useEntry, FILE_HEADER_HEIGHT } from "./entries"
import { findingGroupColor, isAnchored } from "./findings"
import { reviewQueries, type PullRequestRef } from "./queries"
import { useReviewPage } from "./store"

const statusLabel = {
  added: "Added",
  removed: "Deleted",
  renamed: "Renamed",
  modified: null,
} as const

/** The sticky bar above each file: where it is, what it holds, and whether you've read it. */
export function FileHeader({ pr, id }: { pr: PullRequestRef; id: string }) {
  const entry = useEntry(id)
  const path = entry?.file.path ?? ""
  const viewed = useReviewPage((state) => state.viewed.has(path))
  const flipped = useReviewPage((state) => state.collapsed.has(path))
  const toggleViewed = useReviewPage((state) => state.toggleViewed)
  const toggleCollapsed = useReviewPage((state) => state.toggleCollapsed)
  const askInChat = useReviewPage((state) => state.askInChat)
  const detail = useQuery(reviewQueries.detail(pr)).data
  const conversation = useQuery(reviewQueries.conversation(pr)).data
  if (!entry) return null
  const { file } = entry
  const renderable = isRenderable(file)
  const collapsed = !renderable || viewed !== flipped
  const findings = (detail?.findings ?? []).filter(
    (finding) => finding.file === path && finding.status === "open" && isAnchored(finding)
  )
  const threads = (conversation?.threads ?? []).filter(
    (thread) => thread.path === path && !thread.resolved && !thread.outdated
  ).length
  const slash = path.lastIndexOf("/")
  const dir = slash >= 0 ? path.slice(0, slash + 1) : ""
  const name = path.slice(slash + 1)
  const status = statusLabel[file.status]
  const worst = findings.find((f) => f.group === "bug") ?? findings[0]

  return (
    <div
      data-file-header={path}
      onPointerEnter={() => {
        if (renderable)
          loadReviewFileContents(pr.owner, pr.repo, pr.number, file).catch((error: unknown) =>
            console.warn("Could not prefetch file contents", { path, error })
          )
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget && renderable) toggleCollapsed(path)
      }}
      style={{ height: FILE_HEADER_HEIGHT }}
      className={cn(
        "group/header flex items-center gap-2 border-b border-border bg-[color-mix(in_oklab,var(--background)_94%,var(--foreground))] px-2.5 font-sans text-xs",
        collapsed && "border-b-transparent"
      )}
    >
      <button
        type="button"
        aria-label={collapsed ? `Expand ${path}` : `Collapse ${path}`}
        aria-expanded={!collapsed}
        disabled={!renderable}
        onClick={() => toggleCollapsed(path)}
        className="flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-40"
      >
        <CaretDownIcon className={cn("size-3.5 transition-transform", collapsed && "-rotate-90")} />
      </button>
      {entry.step && (
        <span
          title={`Step ${entry.step.index}: ${entry.step.title}`}
          className="flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/12 text-[10px] font-semibold text-primary tabular-nums"
        >
          {entry.step.index}
        </span>
      )}
      <span className="flex min-w-0 items-baseline font-mono text-[12px]" title={path}>
        <span className="truncate text-left text-muted-foreground [direction:rtl]">
          <bdi>{dir}</bdi>
        </span>
        <span className={cn("shrink-0 font-medium text-foreground", viewed && "text-muted-foreground")}>
          {name}
        </span>
      </span>
      <button
        type="button"
        aria-label={`Copy ${path}`}
        onClick={() =>
          void navigator.clipboard.writeText(path).then(() => toast.success("Copied the path"))
        }
        className="hidden size-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:text-foreground group-hover/header:flex"
      >
        <CopyIcon className="size-3" />
      </button>
      {status && (
        <span className="shrink-0 rounded-[4px] border border-border px-1 text-[10px] leading-4 text-muted-foreground">
          {status}
        </span>
      )}
      <span className="flex shrink-0 gap-1.5 font-mono text-[11px] tabular-nums">
        {entry.additions > 0 && <span className="text-success-foreground">+{entry.additions}</span>}
        {entry.deletions > 0 && <span className="text-destructive-foreground">−{entry.deletions}</span>}
      </span>
      {worst && (
        <span
          className="flex shrink-0 items-center gap-1 text-[11px]"
          style={{ color: findingGroupColor[worst.group] }}
          title={`${findings.length} open finding${findings.length === 1 ? "" : "s"} from Open SWE`}
        >
          <AgentMark className="size-3" />
          {findings.length}
        </span>
      )}
      {threads > 0 && (
        <span
          className="flex shrink-0 items-center gap-1 text-[11px] text-muted-foreground"
          title={`${threads} open conversation${threads === 1 ? "" : "s"}`}
        >
          <ChatCircleIcon className="size-3" />
          {threads}
        </span>
      )}
      {!renderable && (
        <span className="truncate text-muted-foreground">Binary or too large to show</span>
      )}
      <span className="flex-1" onClick={() => renderable && toggleCollapsed(path)} />
      <button
        type="button"
        onClick={() => askInChat(`About \`${path}\` in this pull request: `)}
        className="hidden shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-muted-foreground hover:bg-accent hover:text-foreground group-hover/header:flex"
      >
        <AgentMark className="size-3" />
        Ask
      </button>
      <a
        href={`https://github.com/${pr.owner}/${pr.repo}/blob/${file.headSha}/${path}`}
        target="_blank"
        rel="noreferrer"
        aria-label={`View ${path} on GitHub`}
        className="hidden size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground group-hover/header:flex"
      >
        <ArrowSquareOutIcon className="size-3.5" />
      </a>
      <button
        type="button"
        role="checkbox"
        aria-checked={viewed}
        onClick={() => toggleViewed(path)}
        className={cn(
          "flex shrink-0 items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] transition-colors",
          viewed
            ? "border-transparent bg-primary/12 text-primary"
            : "border-border text-muted-foreground hover:text-foreground"
        )}
      >
        <span
          className={cn(
            "flex size-3 items-center justify-center rounded-[3px] border",
            viewed ? "border-primary bg-primary text-primary-foreground" : "border-muted-foreground/60"
          )}
        >
          {viewed && <CheckIcon weight="bold" className="size-2.5" />}
        </span>
        Viewed
      </button>
    </div>
  )
}
