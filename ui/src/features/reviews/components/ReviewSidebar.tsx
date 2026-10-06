import { memo, useCallback, useEffect, useMemo } from "react"
import {
  FileTree,
  useFileTree,
  useFileTreeSelection,
} from "@pierre/trees/react"
import type { ReactNode } from "react"

import { ListSidebarTitle } from "@langchain/gtm-platform-design-system/patterns/split-view"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import { Box, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import {
  Tabs,
  TabsList,
  TabsTrigger,
} from "@langchain/gtm-platform-design-system/ui/tabs"

import { GitPullRequest, List, TreeStructure } from "@/components/glyphs"

import type {
  FileTreeDirectoryHandle,
  GitStatus,
  GitStatusEntry,
} from "@pierre/trees"
import type { ReviewDiffFile } from "@/lib/api"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
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

const ROW_CLASS =
  "flex w-full cursor-pointer items-start gap-2 rounded-badge px-2 py-1.5 text-left text-label outline-none focus-visible:ring-2 focus-visible:ring-primary"

function OverviewRow({
  active,
  onSelect,
}: {
  active: boolean
  onSelect: () => void
}) {
  return (
    <Box className="px-2">
      <button
        type="button"
        aria-current={active ? "true" : undefined}
        onClick={onSelect}
        className={cn(
          ROW_CLASS,
          active
            ? "bg-selected font-medium text-ink"
            : "text-ink-subtle hover:bg-hover hover:text-ink"
        )}
      >
        Overview
      </button>
    </Box>
  )
}

function isSidebarView(value: unknown): value is ReviewSidebarView {
  return value === "ai" || value === "files"
}

export function ReviewSidebarPanel({ data }: { data: ReviewSidebarData }) {
  const hasGroups = data.groups !== null && data.groups.length > 0
  const showAi = data.view === "ai" && hasGroups

  return (
    <Stack gap="xs" className="min-h-0 flex-1 pt-2 pb-2">
      <ListSidebarTitle icon={GitPullRequest} className="px-4">
        {data.title}
      </ListSidebarTitle>
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
        <Stack gap="sm" className="px-4 pt-1">
          {[0, 1, 2, 3].map((index) => (
            <Skeleton key={index} className="h-4 w-full" />
          ))}
        </Stack>
      ) : (
        <ReviewFileTreeExplorer
          files={data.files}
          selected={data.selected}
          onSelect={data.onSelect}
        />
      )}
    </Stack>
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
      onValueChange={(value: unknown) => {
        if (isSidebarView(value)) onChange(value)
      }}
      className="px-3"
    >
      <TabsList variant="line" aria-label="Sidebar view" className="w-full">
        <ReviewViewTab value="ai" label="Walkthrough" count={stepCount}>
          <Icon icon={List} size="sm" />
        </ReviewViewTab>
        <ReviewViewTab value="files" label="Files" count={fileCount}>
          <Icon icon={TreeStructure} size="sm" />
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
      {count !== null && <Badge tier="chip">{count}</Badge>}
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
    <Stack gap="none" className="min-h-0 flex-1 overflow-y-auto px-2 py-1">
      {groups.map((group) => (
        <ReviewGroupRow
          key={group.index}
          group={group}
          active={group.index === activeGroup}
          onSelectGroup={onSelectGroup}
        />
      ))}
    </Stack>
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
          className="rounded-tick bg-muted px-1 py-0.5 font-mono text-meta text-ink"
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
      className={cn(ROW_CLASS, active ? "bg-selected" : "hover:bg-hover")}
    >
      <span className="mt-px shrink-0 font-mono text-meta text-ink-subtle tabular-nums">
        {group.index}.
      </span>
      <span
        className={cn(
          "min-w-0 text-label",
          active ? "font-medium text-ink" : "text-ink-muted"
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
    <Box className="min-h-0 flex-1">
      <FileTree
        model={model}
        style={
          {
            height: "100%",
            ...treeThemeStyle(),
            // Must stay opaque: the tree's truncation marker ("…") paints
            // this color behind itself to hide the overflowing filename.
            "--trees-theme-sidebar-bg": "var(--gtm-sidebar)",
          } as React.CSSProperties
        }
      />
    </Box>
  )
}
