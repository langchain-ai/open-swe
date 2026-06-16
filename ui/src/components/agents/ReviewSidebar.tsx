import { createContext, useContext, useEffect, useMemo, useState } from "react"
import {
  FileTree,
  useFileTree,
  useFileTreeSelection,
} from "@pierre/trees/react"
import {
  CaretRightIcon,
  ListBulletsIcon,
  TreeViewIcon,
} from "@phosphor-icons/react"

import type { GitStatus, GitStatusEntry } from "@pierre/trees"
import type { ReviewDiffFile } from "@/lib/api"
import { Markdown } from "@/components/agents/ported"
import { Skeleton } from "@/components/ui/skeleton"
import { treeThemeStyle } from "@/components/agents/AgentGitPanel"
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
  summary: string
  additions: number
  deletions: number
  fileCount: number
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
}

const ReviewSidebarContext = createContext<{
  data: ReviewSidebarData | null
  setData: (data: ReviewSidebarData | null) => void
} | null>(null)

export function ReviewSidebarProvider({
  children,
}: {
  children: React.ReactNode
}) {
  const [data, setData] = useState<ReviewSidebarData | null>(null)
  const value = useMemo(() => ({ data, setData }), [data])
  return (
    <ReviewSidebarContext.Provider value={value}>
      {children}
    </ReviewSidebarContext.Provider>
  )
}

export function useReviewSidebarData(): ReviewSidebarData | null {
  return useContext(ReviewSidebarContext)?.data ?? null
}

export function useRegisterReviewSidebar(data: ReviewSidebarData) {
  const setData = useContext(ReviewSidebarContext)?.setData
  useEffect(() => {
    if (!setData) return
    setData(data)
    return () => setData(null)
  }, [setData, data])
}

export function ReviewSidebarPanel({ data }: { data: ReviewSidebarData }) {
  const hasGroups = data.groups !== null && data.groups.length > 0
  const showAi = data.view === "ai" && hasGroups

  return (
    <div className="flex min-h-0 flex-1 flex-col pb-2">
      <div className="flex items-center justify-between gap-2 px-4 py-1">
        <span className="text-[10px] font-semibold tracking-wide text-[var(--ui-text-dim)] uppercase">
          {data.title}
        </span>
        {hasGroups && (
          <ReviewViewToggle view={data.view} onChange={data.onViewChange} />
        )}
      </div>
      {showAi ? (
        <ReviewGroupList
          groups={data.groups ?? []}
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
  onSelectGroup,
}: {
  groups: Array<ReviewSidebarGroup>
  onSelectGroup: (index: number) => void
}) {
  return (
    <div className="min-h-0 flex-1 space-y-1 overflow-y-auto px-2 pb-2">
      {groups.map((group) => (
        <ReviewGroupRow
          key={group.index}
          group={group}
          onSelect={() => onSelectGroup(group.index)}
        />
      ))}
    </div>
  )
}

function ReviewGroupRow({
  group,
  onSelect,
}: {
  group: ReviewSidebarGroup
  onSelect: () => void
}) {
  const [expanded, setExpanded] = useState(false)
  return (
    <div className="rounded-md transition-colors hover:bg-[var(--ui-sidebar-hover)]">
      <button
        type="button"
        onClick={onSelect}
        className="flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left"
      >
        <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded bg-[var(--ui-panel-2)] text-[10px] font-medium text-[var(--ui-text-dim)]">
          {group.index}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-xs font-medium text-[var(--ui-text)]">
            {group.title}
          </span>
          <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-[var(--ui-text-dim)]">
            <span>
              {group.fileCount} file{group.fileCount === 1 ? "" : "s"}
            </span>
            {group.additions > 0 && (
              <span className="text-emerald-500">+{group.additions}</span>
            )}
            {group.deletions > 0 && (
              <span className="text-red-500">-{group.deletions}</span>
            )}
          </span>
        </span>
      </button>
      {group.summary && (
        <div className="px-2 pb-1.5 pl-8">
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            className="inline-flex items-center gap-1 text-[11px] text-[var(--ui-text-dim)] hover:text-[var(--ui-text)]"
          >
            <CaretRightIcon
              className={cn(
                "size-3 transition-transform",
                expanded && "rotate-90"
              )}
            />
            Read explanation
          </button>
          {expanded && (
            <div className="mt-1.5 text-[11px] text-[var(--ui-text-muted)]">
              <Markdown content={group.summary} />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

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
    initialExpansion: "open",
    flattenEmptyDirectories: true,
    icons: "standard",
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
    if (selected) {
      model.scrollToPath(selected, { focus: false })
    }
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
