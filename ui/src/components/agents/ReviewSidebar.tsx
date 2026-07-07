import { memo, useCallback, useEffect, useMemo } from "react"
import {
  FileTree,
  useFileTree,
  useFileTreeSelection,
} from "@pierre/trees/react"
import { ListBulletsIcon, TreeViewIcon } from "@phosphor-icons/react"
import type { ReactNode } from "react"

import type {
  FileTreeDirectoryHandle,
  GitStatus,
  GitStatusEntry,
} from "@pierre/trees"
import type { ReviewDiffFile } from "@/lib/api"
import { Skeleton } from "@/components/ui/skeleton"
import {
  TREE_UNSAFE_CSS,
  treeThemeStyle,
} from "@/components/agents/AgentGitPanel"
import { cn } from "@/lib/utils"

function reviewFileGitStatus(status: ReviewDiffFile["status"]): GitStatus {
  if (status === "removed") return "deleted"
  if (status === "added") return "added"
  if (status === "renamed") return "renamed"
  return "modified"
}

export type ReviewSidebarView = "ai" | "files"

export interface ReviewSidebarGroup {
  index: number
  title: string
}

export interface ReviewSidebarData {
  title: string
  files: Array<ReviewDiffFile> | null
  selected: string | null
  viewed: Set<string>
  onSelect: (path: string) => void
  groups: Array<ReviewSidebarGroup> | null
  view: ReviewSidebarView
  onViewChange: (view: ReviewSidebarView) => void
  onSelectGroup: (index: number) => void
  // The block currently pinned at the top of the diff (scroll-spy), highlighted
  // in the agenda. null when no block is active or the AI view isn't shown.
  activeGroup: number | null
}

export function ReviewSidebarPanel({ data }: { data: ReviewSidebarData }) {
  const hasGroups = data.groups !== null && data.groups.length > 0
  const showAi = data.view === "ai" && hasGroups

  return (
    <div className="flex min-h-0 flex-1 flex-col pb-2">
      <div className="flex items-center justify-between gap-2 px-4 py-1">
        <span className="text-[10px] font-medium tracking-wide text-[var(--ui-text-dim)] uppercase">
          {data.title}
        </span>
        {hasGroups && (
          <ReviewViewToggle view={data.view} onChange={data.onViewChange} />
        )}
      </div>
      {showAi ? (
        <ReviewGroupList
          groups={data.groups ?? []}
          activeGroup={data.activeGroup}
          onSelectGroup={data.onSelectGroup}
        />
      ) : !data.files ? (
        <div className="px-4 pt-1">
          <Skeleton className="h-40 w-full" />
        </div>
      ) : (
        <ReviewFileTreeExplorer
          files={data.files}
          selected={data.selected}
          onSelect={data.onSelect}
        />
      )}
    </div>
  )
}

function ReviewViewToggle({
  view,
  onChange,
}: {
  view: ReviewSidebarView
  onChange: (view: ReviewSidebarView) => void
}) {
  return (
    <div className="flex items-center gap-0.5 rounded-md border border-[var(--ui-border)] p-0.5">
      <ReviewViewToggleButton
        active={view === "ai"}
        label="AI sorted"
        onClick={() => onChange("ai")}
      >
        <ListBulletsIcon className="size-3.5" />
      </ReviewViewToggleButton>
      <ReviewViewToggleButton
        active={view === "files"}
        label="File tree"
        onClick={() => onChange("files")}
      >
        <TreeViewIcon className="size-3.5" />
      </ReviewViewToggleButton>
    </div>
  )
}

function ReviewViewToggleButton({
  active,
  label,
  onClick,
  children,
}: {
  active: boolean
  label: string
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-pressed={active}
      title={label}
      className={cn(
        "flex size-5 items-center justify-center rounded text-[var(--ui-text-dim)] transition-colors",
        active
          ? "bg-[var(--ui-sidebar-hover)] text-[var(--ui-text)]"
          : "hover:text-[var(--ui-text)]"
      )}
    >
      {children}
    </button>
  )
}

function ReviewGroupList({
  groups,
  activeGroup,
  onSelectGroup,
}: {
  groups: Array<ReviewSidebarGroup>
  activeGroup: number | null
  onSelectGroup: (index: number) => void
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto py-1">
      {groups.map((group) => (
        <ReviewGroupRow
          key={group.index}
          group={group}
          active={group.index === activeGroup}
          onSelectGroup={onSelectGroup}
        />
      ))}
    </div>
  )
}

// Render a title with `backtick`-delimited spans as inline code chips, matching
// the Markdown component's inline-code styling, without pulling in the full
// block renderer for a single line.
export function renderInlineCode(text: string): Array<ReactNode> {
  return text.split(/(`[^`]+`)/g).map((part, i) => {
    if (part.length >= 2 && part.startsWith("`") && part.endsWith("`")) {
      return (
        <code
          key={i}
          className="rounded bg-[var(--ui-panel-2)] px-1 py-0.5 font-mono text-[0.9em] text-[var(--ui-accent)]"
        >
          {part.slice(1, -1)}
        </code>
      )
    }
    return <span key={i}>{part}</span>
  })
}

// A single agenda entry: just the block number + title, like a Google-Docs
// outline. Clicking (or Enter/Space) scrolls the diff to that block. The active
// block (scroll-spy) gets an accent rule + emphasis. memo'd so scroll-spy
// re-renders only repaint the rows whose active state actually changed.
const ReviewGroupRow = memo(function ReviewGroupRow({
  group,
  active,
  onSelectGroup,
}: {
  group: ReviewSidebarGroup
  active: boolean
  onSelectGroup: (index: number) => void
}) {
  const title = useMemo(() => renderInlineCode(group.title), [group.title])
  const selectGroup = useCallback(
    () => onSelectGroup(group.index),
    [onSelectGroup, group.index]
  )
  const onKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault()
        onSelectGroup(group.index)
      }
    },
    [onSelectGroup, group.index]
  )

  return (
    <div
      role="button"
      tabIndex={0}
      aria-current={active ? "true" : undefined}
      onClick={selectGroup}
      onKeyDown={onKeyDown}
      className={cn(
        "flex cursor-pointer items-start gap-2 border-l-2 px-3 py-1.5 text-left transition-colors",
        active
          ? "border-[var(--ui-accent)] bg-[var(--ui-sidebar-hover)]"
          : "border-transparent hover:bg-[var(--ui-sidebar-hover)]"
      )}
    >
      <span className="mt-px shrink-0 text-[11px] font-medium text-[var(--ui-text-dim)] tabular-nums">
        {group.index}.
      </span>
      <span
        className={cn(
          "min-w-0 text-xs leading-5",
          active
            ? "font-medium text-[var(--ui-text)]"
            : "text-[var(--ui-text-muted)]"
        )}
      >
        {title}
      </span>
    </div>
  )
})

function ReviewFileTreeExplorer({
  files,
  selected,
  onSelect,
}: {
  files: Array<ReviewDiffFile>
  selected: string | null
  onSelect: (path: string) => void
}) {
  const paths = useMemo(() => files.map((file) => file.path), [files])
  const gitStatus = useMemo<Array<GitStatusEntry>>(
    () =>
      files.map((file) => ({
        path: file.path,
        status: reviewFileGitStatus(file.status),
      })),
    [files]
  )

  const { model } = useFileTree({
    paths,
    gitStatus,
    flattenEmptyDirectories: true,
    density: "default",
    icons: "complete",
    unsafeCSS: TREE_UNSAFE_CSS,
  })

  useEffect(() => {
    model.resetPaths(paths)
  }, [model, paths])

  useEffect(() => {
    model.setGitStatus(gitStatus)
  }, [model, gitStatus])

  const selection = useFileTreeSelection(model)
  useEffect(() => {
    const path = selection[0]
    if (path) onSelect(path)
  }, [selection, onSelect])

  useEffect(() => {
    if (!selected) return
    const segments = selected.split("/")
    for (let depth = 1; depth < segments.length; depth += 1) {
      const item = model.getItem(segments.slice(0, depth).join("/"))
      if (item?.isDirectory()) (item as FileTreeDirectoryHandle).expand()
    }
    model.scrollToPath(selected, { focus: false })
  }, [model, selected])

  return (
    <div className="min-h-0 flex-1">
      <FileTree
        model={model}
        style={
          {
            height: "100%",
            ...treeThemeStyle(),
            // Must stay opaque: the tree's truncation marker ("…") paints
            // this color behind itself to hide the overflowing filename.
            "--trees-theme-sidebar-bg": "var(--ui-sidebar)",
          } as React.CSSProperties
        }
      />
    </div>
  )
}
