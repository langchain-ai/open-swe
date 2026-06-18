import {
  createContext,
  memo,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react"
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
import type { ReactNode } from "react"

import type {
  FileTreeDirectoryHandle,
  GitStatus,
  GitStatusEntry,
} from "@pierre/trees"
import type { ReviewDiffFile } from "@/lib/api"
import { Markdown } from "@/components/agents/ported"
import { Skeleton } from "@/components/ui/skeleton"
import { TREE_UNSAFE_CSS, treeThemeStyle } from "@/components/agents/AgentGitPanel"
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
  files: Array<string>
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
          onSelectGroup={data.onSelectGroup}
          onSelectFile={data.onSelect}
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
  onSelectFile,
}: {
  groups: Array<ReviewSidebarGroup>
  onSelectGroup: (index: number) => void
  onSelectFile: (path: string) => void
}) {
  return (
    <div className="min-h-0 flex-1 divide-y divide-[var(--ui-border-subtle)] overflow-y-auto">
      {groups.map((group) => (
        <ReviewGroupRow
          key={group.index}
          group={group}
          onSelectGroup={onSelectGroup}
          onSelectFile={onSelectFile}
        />
      ))}
    </div>
  )
}

function splitPath(path: string): { dir: string; base: string } {
  const idx = path.lastIndexOf("/")
  if (idx === -1) return { dir: "", base: path }
  return { dir: path.slice(0, idx), base: path.slice(idx + 1) }
}

// Older stored summaries embed `[label](#loc=path:line)` diff links. Render the
// label as inline code instead so no stale jump-links leak into the explanation.
function stripLocationLinks(summary: string): string {
  return summary.replace(/\[([^\]]+)\]\(#loc=[^)]*\)/g, "`$1`")
}

// Render a title with `backtick`-delimited spans as inline code chips, matching
// the Markdown component's inline-code styling, without pulling in the full
// block renderer for a single line.
function renderInlineCode(text: string): Array<ReactNode> {
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

// The whole card is the scroll-to-group target so clicks anywhere (including
// the expanded explanation body) focus the diff. Nested controls — the file
// links and the "Read explanation" toggle — stop propagation so they keep
// their own behavior. memo'd + memoized string processing so re-renders from
// sibling state don't re-run Markdown/inline-code work.
const ReviewGroupRow = memo(function ReviewGroupRow({
  group,
  onSelectGroup,
  onSelectFile,
}: {
  group: ReviewSidebarGroup
  onSelectGroup: (index: number) => void
  onSelectFile: (path: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const title = useMemo(() => renderInlineCode(group.title), [group.title])
  const summary = useMemo(
    () => stripLocationLinks(group.summary),
    [group.summary]
  )
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
      onClick={selectGroup}
      onKeyDown={onKeyDown}
      className="cursor-pointer px-3 py-3 transition-colors hover:bg-[var(--ui-sidebar-hover)]"
    >
      <div className="flex w-full items-start gap-2 text-left">
        <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded bg-[var(--ui-panel-2)] text-[11px] font-medium text-[var(--ui-text-dim)]">
          {group.index}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-xs leading-5 font-medium text-[var(--ui-text)]">
            {title}
          </span>
          <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-[var(--ui-text-dim)]">
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
      </div>

      {group.files.length > 0 && (
        <div className="mt-2 space-y-0.5 pl-7">
          {group.files.map((path) => {
            const { dir, base } = splitPath(path)
            return (
              <button
                key={path}
                type="button"
                onClick={(event) => {
                  event.stopPropagation()
                  onSelectFile(path)
                }}
                title={path}
                className="flex w-full items-baseline gap-1.5 text-left text-[11px] hover:text-[var(--ui-accent)]"
              >
                <span className="shrink-0 font-medium text-[var(--ui-text-muted)]">
                  {base}
                </span>
                {dir && (
                  <span className="min-w-0 truncate text-[var(--ui-text-dim)]">
                    {dir}
                  </span>
                )}
              </button>
            )
          })}
        </div>
      )}

      {group.summary && (
        <div className="mt-2">
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation()
              setExpanded((value) => !value)
            }}
            className="inline-flex items-center gap-1 text-[11px] font-medium text-[var(--ui-accent)]"
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
            <div className="mt-1.5">
              <Markdown content={summary} />
            </div>
          )}
        </div>
      )}
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
