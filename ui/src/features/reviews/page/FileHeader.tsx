import {
  ArrowSquareOutIcon,
  CaretDownIcon,
  ChatCircleIcon,
  CheckIcon,
  CopyIcon,
} from "@phosphor-icons/react"

import { cn } from "@/lib/utils"
import { copyText } from "@/features/reviews/lib/copyText"
import { githubUrls } from "@/features/reviews/lib/githubUrls"
import { loadReviewFileContents } from "@/features/reviews/lib/fileContents"
import { AgentMark } from "./AgentMark"
import { isRenderable } from "./diffEntries"
import { useEntry, useMarkers } from "./entries"
import { findingGroupColor, findingTarget, threadTarget } from "./findings"
import type { PullRequestRef } from "./queries"
import { isCollapsed, useReviewPage } from "./store"
import { Tag } from "./Tag"
import { plural, splitPath } from "./text"

export const FILE_HEADER_HEIGHT = 40

const statusLabel = {
  added: "Added",
  removed: "Deleted",
  renamed: "Renamed",
  modified: null,
} as const

const iconButton =
  "flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
// Shown while the header is hovered or holds focus, so keyboards reach them too.
const onHover = "hidden group-focus-within/header:flex group-hover/header:flex"

/** The sticky bar above each file: where it is, what it holds, and whether you've read it. */
export function FileHeader({ pr, id }: { pr: PullRequestRef; id: string }) {
  const entry = useEntry(id)
  const path = entry?.file.path ?? ""
  const viewed = useReviewPage((state) => state.viewed.has(path))
  const collapsed = useReviewPage((state) => isCollapsed(state, path))
  const markViewed = useReviewPage((state) => state.markViewed)
  const toggleCollapsed = useReviewPage((state) => state.toggleCollapsed)
  const askInChat = useReviewPage((state) => state.askInChat)
  const jumpTo = useReviewPage((state) => state.jumpTo)
  const showFinding = useReviewPage((state) => state.showFinding)
  const { findings, threads } = useMarkers(path)
  if (!entry) return null
  const { file } = entry
  const renderable = isRenderable(file)
  const folded = !renderable || collapsed
  const { dir, name } = splitPath(path)
  const status = statusLabel[file.status]
  const worst = findings[0]
  const firstThread = threads[0]

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
      // Clicking the bar's empty space folds the file, as on GitHub.
      onClick={(event) => {
        if (
          renderable &&
          event.target instanceof Element &&
          !event.target.closest("button, a")
        )
          toggleCollapsed(path)
      }}
      style={{ height: FILE_HEADER_HEIGHT }}
      className={cn(
        "group/header flex items-center gap-2 border-b border-border bg-[color-mix(in_oklab,var(--background)_94%,var(--foreground))] px-2.5 font-sans text-xs",
        folded && "border-b-transparent"
      )}
    >
      <button
        type="button"
        aria-label={folded ? `Expand ${path}` : `Collapse ${path}`}
        aria-expanded={!folded}
        disabled={!renderable}
        onClick={() => toggleCollapsed(path)}
        className={cn(iconButton, "disabled:opacity-40")}
      >
        <CaretDownIcon
          className={cn(
            "size-3.5 transition-transform",
            folded && "-rotate-90"
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
        onClick={() => copyText(path, "Copied the path")}
        className={cn(iconButton, onHover, "size-5")}
      >
        <CopyIcon className="size-3" />
      </button>
      {status && <Tag className="max-sm:hidden">{status}</Tag>}
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
          onClick={() => showFinding(worst.id, findingTarget(worst))}
          className="flex shrink-0 items-center gap-1 rounded px-1 text-[11px] hover:bg-accent"
          style={{ color: findingGroupColor[worst.group] }}
          title={`${plural(findings.length, "open finding")} from Open SWE; go to the worst`}
        >
          <AgentMark className="size-3" />
          {findings.length}
        </button>
      )}
      {firstThread && (
        <button
          type="button"
          onClick={() => jumpTo(threadTarget(firstThread))}
          className="flex shrink-0 items-center gap-1 rounded px-1 text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground"
          title={`${plural(threads.length, "open conversation")}; go to the first`}
        >
          <ChatCircleIcon className="size-3" />
          {threads.length}
        </button>
      )}
      {!renderable && (
        <span className="truncate text-muted-foreground">
          Binary or too large to show
        </span>
      )}
      <span className="flex-1" />
      <button
        type="button"
        onClick={() => askInChat(`About \`${path}\` in this pull request: `)}
        aria-label={`Ask Open SWE about ${path}`}
        className={cn(
          onHover,
          "shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-muted-foreground hover:bg-accent hover:text-foreground"
        )}
      >
        <AgentMark className="size-3" />
        Ask
      </button>
      <a
        href={githubUrls.file(pr, file.headSha, path)}
        target="_blank"
        rel="noreferrer"
        aria-label={`View ${path} on GitHub`}
        className={cn(iconButton, onHover)}
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
