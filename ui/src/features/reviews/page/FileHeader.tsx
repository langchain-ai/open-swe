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
import {
  findingGroupColor,
  isAnchored,
  threadsNeedingAttention,
} from "./findings"
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
  const markViewed = useReviewPage((state) => state.markViewed)
  const toggleCollapsed = useReviewPage((state) => state.toggleCollapsed)
  const askInChat = useReviewPage((state) => state.askInChat)
  const jumpTo = useReviewPage((state) => state.jumpTo)
  const setExpandedFinding = useReviewPage((state) => state.setExpandedFinding)
  const detail = useQuery(reviewQueries.detail(pr)).data
  const conversation = useQuery(reviewQueries.conversation(pr)).data
  if (!entry) return null
  const { file } = entry
  const renderable = isRenderable(file)
  const collapsed = !renderable || viewed !== flipped
  const findings = (detail?.findings ?? []).filter(
    (finding) =>
      finding.file === path && finding.status === "open" && isAnchored(finding)
  )
  const openThreads = threadsNeedingAttention(
    conversation?.threads ?? [],
    detail?.findings ?? []
  ).filter((thread) => thread.path === path)
  const threads = openThreads.length
  const slash = path.lastIndexOf("/")
  const dir = slash >= 0 ? path.slice(0, slash + 1) : ""
  const name = path.slice(slash + 1)
  const status = statusLabel[file.status]
  const worst = findings.find((f) => f.group === "bug") ?? findings[0]

  return (
    <div
      data-file-header={path}
      onPointerEnter={() => {
        // Only a changed file has unchanged context to expand into.
        if (
          renderable &&
          (file.status === "modified" || file.status === "renamed")
        )
          loadReviewFileContents(pr.owner, pr.repo, pr.number, file).catch(
            (error: unknown) =>
              console.warn("Could not prefetch file contents", { path, error })
          )
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget && renderable)
          toggleCollapsed(path)
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
        <CaretDownIcon
          className={cn(
            "size-3.5 transition-transform",
            collapsed && "-rotate-90"
          )}
        />
      </button>
      {entry.step && (
        <span
          title={`Step ${entry.step.index}: ${entry.step.title}`}
          className="flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/12 text-[10px] font-semibold text-primary tabular-nums"
        >
          {entry.step.index}
        </span>
      )}
      <span
        className="flex min-w-0 items-baseline overflow-hidden font-mono text-[12px]"
        title={path}
      >
        <span className="truncate text-left text-muted-foreground [direction:rtl] max-sm:hidden">
          <bdi>{dir}</bdi>
        </span>
        <span
          className={cn(
            "min-w-[6ch] shrink-[0.01] truncate font-medium text-foreground",
            viewed && "text-muted-foreground"
          )}
        >
          {name}
        </span>
      </span>
      <button
        type="button"
        aria-label={`Copy ${path}`}
        onClick={() =>
          void navigator.clipboard
            .writeText(path)
            .then(() => toast.success("Copied the path"))
        }
        className="hidden size-5 shrink-0 items-center justify-center rounded text-muted-foreground group-focus-within/header:flex group-hover/header:flex hover:text-foreground"
      >
        <CopyIcon className="size-3" />
      </button>
      {status && (
        <span className="shrink-0 rounded-[4px] border border-border px-1 text-[10px] leading-4 text-muted-foreground max-sm:hidden">
          {status}
        </span>
      )}
      <span className="flex shrink-0 gap-1.5 font-mono text-[11px] tabular-nums">
        {entry.additions > 0 && (
          <span className="text-success-foreground">+{entry.additions}</span>
        )}
        {entry.deletions > 0 && (
          <span className="text-destructive-foreground">
            −{entry.deletions}
          </span>
        )}
      </span>
      {worst && (
        <button
          type="button"
          onClick={() => {
            setExpandedFinding(worst.id)
            jumpTo({
              kind: "line",
              path,
              line: worst.end_line ?? 1,
              start: worst.start_line ?? undefined,
              side: worst.side,
            })
          }}
          className="flex shrink-0 items-center gap-1 rounded px-1 text-[11px] hover:bg-accent"
          style={{ color: findingGroupColor[worst.group] }}
          title={`${findings.length} open finding${findings.length === 1 ? "" : "s"} from Open SWE; go to the first`}
        >
          <AgentMark className="size-3" />
          {findings.length}
        </button>
      )}
      {threads > 0 && (
        <button
          type="button"
          onClick={() => {
            const first = openThreads[0]
            if (first?.line != null)
              jumpTo({
                kind: "line",
                path,
                line: first.line,
                side: first.side,
              })
          }}
          className="flex shrink-0 items-center gap-1 rounded px-1 text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground"
          title={`${threads} open conversation${threads === 1 ? "" : "s"}; go to the first`}
        >
          <ChatCircleIcon className="size-3" />
          {threads}
        </button>
      )}
      {!renderable && (
        <span className="truncate text-muted-foreground">
          Binary or too large to show
        </span>
      )}
      <span
        className="flex-1"
        onClick={() => renderable && toggleCollapsed(path)}
      />
      <button
        type="button"
        onClick={() => askInChat(`About \`${path}\` in this pull request: `)}
        aria-label={`Ask Open SWE about ${path}`}
        className="hidden shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-muted-foreground group-focus-within/header:flex group-hover/header:flex hover:bg-accent hover:text-foreground"
      >
        <AgentMark className="size-3" />
        Ask
      </button>
      <a
        href={`https://github.com/${pr.owner}/${pr.repo}/blob/${file.headSha}/${path}`}
        target="_blank"
        rel="noreferrer"
        aria-label={`View ${path} on GitHub`}
        className="hidden size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground group-focus-within/header:flex group-hover/header:flex hover:bg-accent hover:text-foreground"
      >
        <ArrowSquareOutIcon className="size-3.5" />
      </a>
      <button
        type="button"
        role="checkbox"
        aria-checked={viewed}
        aria-label={`Viewed ${path}`}
        onClick={() => markViewed(id)}
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
            viewed
              ? "border-primary bg-primary text-primary-foreground"
              : "border-muted-foreground/60"
          )}
        >
          {viewed && <CheckIcon weight="bold" className="size-2.5" />}
        </span>
        <span className="max-sm:hidden">Viewed</span>
      </button>
    </div>
  )
}
