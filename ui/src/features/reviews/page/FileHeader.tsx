import { CaretDownIcon, CheckIcon } from "@langchain/macaw-components/icons"
import { Button } from "@langchain/macaw-components/Button"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { ArrowSquareOutIcon } from "@phosphor-icons/react/dist/ssr/ArrowSquareOut"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/ssr/ChatCircle"
import { CopyIcon } from "@phosphor-icons/react/dist/ssr/Copy"

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

const quiet = { size: "xs", color: "secondary", variant: "plain" } as const
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
        "group/header flex items-center gap-space-2 border-b border-default bg-surface-level-2 px-space-2 font-sans text-xs",
        folded && "border-b-transparent"
      )}
    >
      <IconButton
        icon={CaretDownIcon}
        label={folded ? `Expand ${path}` : `Collapse ${path}`}
        aria-expanded={!folded}
        disabled={!renderable}
        onClick={() => toggleCollapsed(path)}
        tooltipProps={{ disabled: true }}
        iconClassName={cn("transition-transform", folded && "-rotate-90")}
        {...quiet}
      />
      {entry.step && (
        <span
          title={`Step ${entry.step.index}: ${entry.step.title}`}
          className="flex size-5 shrink-0 items-center justify-center rounded-full bg-brand-subtle text-xxs font-semibold text-brand-primary tabular-nums"
        >
          {entry.step.index}
        </span>
      )}
      <span
        className="flex min-w-0 items-baseline overflow-hidden font-mono text-xxs"
        title={path}
      >
        <span className="truncate text-left text-secondary [direction:rtl] max-sm:hidden">
          <bdi>{dir}</bdi>
        </span>
        <span
          className={cn(
            "min-w-[6ch] shrink-[0.01] truncate font-medium text-primary",
            viewed && "text-secondary"
          )}
        >
          {name}
        </span>
      </span>
      <IconButton
        icon={CopyIcon}
        label={`Copy ${path}`}
        onClick={() => copyText(path, "Copied the path")}
        className={onHover}
        {...quiet}
        size="xxs"
      />
      {status && <Tag className="max-sm:hidden">{status}</Tag>}
      <span className="flex shrink-0 gap-space-2 font-mono text-xxs tabular-nums">
        {entry.additions > 0 && (
          <span className="text-success-secondary">+{entry.additions}</span>
        )}
        {entry.deletions > 0 && (
          <span className="text-error-secondary">−{entry.deletions}</span>
        )}
      </span>
      {worst && (
        <button
          type="button"
          onClick={() => showFinding(worst.id, findingTarget(worst))}
          className="flex shrink-0 items-center gap-space-1 rounded-sm px-space-1 text-xxs hover:bg-surface-level-1-hover"
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
          className="flex shrink-0 items-center gap-space-1 rounded-sm px-space-1 text-xxs text-secondary hover:bg-surface-level-1-hover hover:text-primary"
          title={`${plural(threads.length, "open conversation")}; go to the first`}
        >
          <ChatCircleIcon className="size-3" weight="regular" />
          {threads.length}
        </button>
      )}
      {!renderable && (
        <span className="truncate text-secondary">
          Binary or too large to show
        </span>
      )}
      <span className="flex-1" />
      <Button
        size="xs"
        color="secondary"
        variant="plain"
        onClick={() => askInChat(`About \`${path}\` in this pull request: `)}
        aria-label={`Ask Open SWE about ${path}`}
        className={onHover}
      >
        <AgentMark className="size-3" />
        Ask
      </Button>
      <IconButton
        asChild
        icon={ArrowSquareOutIcon}
        label={`View ${path} on GitHub`}
        className={onHover}
        {...quiet}
      >
        <a
          href={githubUrls.file(pr, file.headSha, path)}
          target="_blank"
          rel="noreferrer"
        />
      </IconButton>
      <button
        type="button"
        role="checkbox"
        aria-checked={viewed}
        aria-label={`Viewed ${path}`}
        onClick={() => markViewed(id)}
        className={cn(
          "flex shrink-0 items-center gap-space-2 rounded-md border px-space-2 py-space-1 text-xxs transition-colors",
          viewed
            ? "border-transparent bg-brand-subtle text-brand-primary"
            : "border-default text-secondary hover:text-primary"
        )}
      >
        <span
          className={cn(
            "flex size-3 items-center justify-center rounded-xs border",
            viewed
              ? "border-brand bg-brand text-brand-on-fill"
              : "border-strong"
          )}
        >
          {viewed && <CheckIcon weight="bold" className="size-2.5" />}
        </span>
        <span className="max-sm:hidden">Viewed</span>
      </button>
    </div>
  )
}
