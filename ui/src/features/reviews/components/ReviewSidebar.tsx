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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  TREE_UNSAFE_CSS,
  treeThemeStyle,
} from "@/features/agents/components/DiffFilesView"
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
  /** Scrolls back to the PR description at the top of the page. */
  onSelectOverview: () => void
}

function OverviewRow({
  active,
  onSelect,
}: {
  active: boolean
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      aria-current={active ? "true" : undefined}
      onClick={onSelect}
      className={cn(
        "flex w-full items-start gap-2 border-l-2 px-3 py-1.5 text-left text-xs leading-5 transition-colors",
        active
          ? "border-primary bg-sidebar-row-hover font-medium text-foreground"
          : "border-transparent text-muted-foreground hover:bg-sidebar-row-hover"
      )}
    >
      Overview
    </button>
  )
}

export function ReviewSidebarPanel({ data }: { data: ReviewSidebarData }) {
  const hasGroups = data.groups !== null && data.groups.length > 0
  const showAi = data.view === "ai" && hasGroups

  return (
    <div className="flex min-h-0 flex-1 flex-col pb-2">
      <div className="px-4 py-1">
        <span className="text-[10px] font-medium tracking-wide text-muted-foreground/70 uppercase">
          {data.title}
        </span>
      </div>
      {hasGroups && (
        <ReviewViewTabs
          view={data.view}
          onChange={data.onViewChange}
          stepCount={data.groups?.length ?? 0}
          fileCount={data.files?.length ?? null}
        />
      )}
      <OverviewRow
        active={showAi && data.activeGroup === null}
        onSelect={data.onSelectOverview}
      />
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

function ReviewViewTabs({
  view,
  onChange,
  stepCount,
  fileCount,
}: {
  view: ReviewSidebarView
  onChange: (view: ReviewSidebarView) => void
  stepCount: number
  fileCount: number | null
}) {
  return (
    <Tabs
      value={view}
      onValueChange={(next) => onChange(next === "files" ? "files" : "ai")}
      className="mx-3 mb-1 border-b border-border"
    >
      <TabsList aria-label="Sidebar view" variant="line" className="w-full">
        <ReviewViewTab value="ai" label="Walkthrough" count={stepCount}>
          <ListBulletsIcon />
        </ReviewViewTab>
        <ReviewViewTab value="files" label="Files" count={fileCount}>
          <TreeViewIcon />
        </ReviewViewTab>
      </TabsList>
    </Tabs>
  )
}

function ReviewViewTab({
  value,
  label,
  count,
  children,
}: {
  value: ReviewSidebarView
  label: string
  count: number | null
  children: ReactNode
}) {
  return (
    <TabsTrigger value={value}>
      {children}
      {label}
      {count !== null && (
        <span className="font-normal text-muted-foreground/70 tabular-nums">
          {count}
        </span>
      )}
    </TabsTrigger>
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
          className="rounded bg-accent px-1 py-0.5 font-mono text-[0.9em] text-primary"
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
  return (
    <button
      type="button"
      aria-current={active ? "true" : undefined}
      onClick={selectGroup}
      className={cn(
        "flex w-full cursor-pointer items-start gap-2 border-l-2 px-3 py-1.5 text-left transition-colors",
        active
          ? "border-primary bg-sidebar-row-hover"
          : "border-transparent hover:bg-sidebar-row-hover"
      )}
    >
      <span className="mt-px shrink-0 text-[11px] font-medium text-muted-foreground/70 tabular-nums">
        {group.index}.
      </span>
      <span
        className={cn(
          "min-w-0 text-xs leading-5",
          active ? "font-medium text-foreground" : "text-muted-foreground"
        )}
      >
        {title}
      </span>
    </button>
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
            "--trees-theme-sidebar-bg": "var(--sidebar)",
          } as React.CSSProperties
        }
      />
    </div>
  )
}
