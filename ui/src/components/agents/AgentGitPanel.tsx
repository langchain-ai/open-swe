import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { MultiFileDiff } from "@pierre/diffs/react"
import {
  FileTree,
  useFileTree,
  useFileTreeSelection,
} from "@pierre/trees/react"
import {
  ArrowSquareOutIcon,
  ArrowsInIcon,
  ArrowsOutIcon,
  CaretDownIcon,
  GitPullRequestIcon,
  SidebarSimpleIcon,
} from "@phosphor-icons/react"
import type { GitStatus, GitStatusEntry } from "@pierre/trees"

import type { AgentThread, Message } from "@/lib/agents/types"
import type { ThreadPrDiffFile } from "@/lib/agents/api"
import { useAgentThreadPrDiff } from "@/lib/agents/queries"
import { buttonVariants } from "@/components/ui/button"
import type { ChangedFileSummaryItem } from "@/components/agents/messages"
import { useDiffOptions } from "@/components/agents/utils/diffUtils"
import { summarizeChangedFiles } from "@/components/agents/ported"
import { Z } from "@/components/agents/z-index"
import { cn } from "@/lib/utils"

interface AgentGitPanelProps {
  thread: AgentThread
  messages: Array<Message>
}

interface PanelFile {
  filePath: string
  treePath: string
  additions: number
  deletions: number
  originalContent: string
  modifiedContent: string
  status: GitStatus
  unrenderable?: boolean
}

function prFileStatus(file: ThreadPrDiffFile): GitStatus {
  if (file.status === "added") return "added"
  if (file.status === "removed") return "deleted"
  return "modified"
}

function deriveStatus(file: ChangedFileSummaryItem): GitStatus {
  if (file.originalContent.length === 0 && file.modifiedContent.length > 0) {
    return "added"
  }
  if (file.modifiedContent.length === 0 && file.originalContent.length > 0) {
    return "deleted"
  }
  return "modified"
}

function commonDirPrefix(paths: Array<string>): string {
  const first = paths[0]
  if (paths.length === 0 || first === undefined) return ""
  const base = first.split("/").slice(0, -1)
  let depth = base.length
  for (const path of paths) {
    const segments = path.split("/").slice(0, -1)
    let i = 0
    while (i < depth && i < segments.length && segments[i] === base[i]) i++
    depth = i
  }
  return depth === 0 ? "" : `${base.slice(0, depth).join("/")}/`
}

const PANEL_STORAGE_WIDTH = "open-swe.gitpanel.width"
const PANEL_DEFAULT_WIDTH = 420
const PANEL_MIN_WIDTH = 320
const PANEL_MAX_WIDTH = 720

function readStoredPanelWidth(): number {
  if (typeof window === "undefined") return PANEL_DEFAULT_WIDTH
  const raw = window.localStorage.getItem(PANEL_STORAGE_WIDTH)
  const parsed = raw ? Number(raw) : NaN
  if (!Number.isFinite(parsed)) return PANEL_DEFAULT_WIDTH
  return Math.min(PANEL_MAX_WIDTH, Math.max(PANEL_MIN_WIDTH, parsed))
}

function PanelResizeHandle({
  width,
  onResize,
}: {
  width: number
  onResize: (next: number) => void
}) {
  const startRef = useRef<{ x: number; width: number } | null>(null)
  const [dragging, setDragging] = useState(false)

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    startRef.current = { x: e.clientX, width }
    setDragging(true)
    e.currentTarget.setPointerCapture(e.pointerId)
  }

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!startRef.current) return
    onResize(startRef.current.width - (e.clientX - startRef.current.x))
  }

  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    startRef.current = null
    setDragging(false)
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }
  }

  useEffect(() => {
    if (!dragging) return
    const prev = document.body.style.cursor
    document.body.style.cursor = "col-resize"
    return () => {
      document.body.style.cursor = prev
    }
  }, [dragging])

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      className={cn(
        "absolute top-0 left-0 z-20 h-full w-1 cursor-col-resize touch-none select-none",
        "after:absolute after:inset-y-0 after:left-0 after:w-px after:bg-transparent after:transition-colors",
        "hover:after:bg-[var(--ui-border)]",
        dragging && "after:bg-[var(--ui-border)]"
      )}
    />
  )
}

function treeThemeStyle(): React.CSSProperties {
  return {
    "--trees-theme-sidebar-bg": "var(--ui-surface)",
    "--trees-theme-sidebar-fg": "var(--ui-text)",
    "--trees-theme-sidebar-border": "var(--ui-border)",
    "--trees-theme-sidebar-header-fg": "var(--ui-text-dim)",
    "--trees-theme-list-hover-bg": "var(--ui-panel-2)",
    "--trees-theme-list-active-selection-bg": "var(--ui-accent-bubble)",
    "--trees-theme-list-active-selection-fg": "var(--ui-text)",
    "--trees-theme-input-bg": "var(--ui-panel)",
    "--trees-theme-input-border": "var(--ui-border)",
    "--trees-theme-focus-ring": "var(--ui-accent)",
    "--trees-theme-scrollbar-thumb": "var(--ui-border)",
    "--trees-theme-git-added-fg": "var(--ui-success)",
    "--trees-theme-git-deleted-fg": "var(--ui-danger)",
    "--trees-theme-git-modified-fg": "var(--ui-accent)",
  } as React.CSSProperties
}

export function AgentGitPanel({ thread, messages }: AgentGitPanelProps) {
  const [topTab, setTopTab] = useState<"git" | "desktop" | "terminal">("git")
  const [tab, setTab] = useState<"diff" | "review" | "commits">("diff")
  const [collapsed, setCollapsed] = useState(true)
  const [width, setWidthState] = useState(() => readStoredPanelWidth())
  const [fullScreen, setFullScreen] = useState(false)

  const setWidth = useCallback((next: number) => {
    const clamped = Math.min(PANEL_MAX_WIDTH, Math.max(PANEL_MIN_WIDTH, next))
    setWidthState(clamped)
    window.localStorage.setItem(PANEL_STORAGE_WIDTH, String(clamped))
  }, [])
  const [selectedTreePath, setSelectedTreePath] = useState<string | null>(null)
  const sectionRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const pr = thread.pr

  // Always start collapsed; re-collapse when switching threads, and
  // uncollapse when a PR lands mid-session.
  const [prSeen, setPrSeen] = useState<{ threadId: string; hadPr: boolean }>(
    () => ({ threadId: thread.id, hadPr: Boolean(pr) })
  )
  if (prSeen.threadId !== thread.id) {
    setPrSeen({ threadId: thread.id, hadPr: Boolean(pr) })
    setCollapsed(true)
  } else if (pr && !prSeen.hadPr) {
    setPrSeen({ threadId: thread.id, hadPr: true })
    setCollapsed(false)
  }

  const prDiff = useAgentThreadPrDiff(thread.id, Boolean(pr))

  const chunks = useMemo(
    () => messages.flatMap((message) => message.chunks),
    [messages]
  )

  const files = useMemo<Array<PanelFile>>(() => {
    if (prDiff.data) {
      return prDiff.data.files.map((file) => ({
        filePath: file.path,
        treePath: file.path,
        additions: file.additions,
        deletions: file.deletions,
        originalContent: file.originalContent ?? "",
        modifiedContent: file.modifiedContent ?? "",
        status: prFileStatus(file),
        unrenderable: file.unrenderable,
      }))
    }
    const summary = summarizeChangedFiles(chunks)
    const prefix = commonDirPrefix(summary.map((file) => file.filePath))
    return summary.map((file) => ({
      filePath: file.filePath,
      treePath:
        prefix && file.filePath.startsWith(prefix)
          ? file.filePath.slice(prefix.length)
          : file.filePath,
      additions: file.additions,
      deletions: file.deletions,
      originalContent: file.originalContent,
      modifiedContent: file.modifiedContent,
      status: deriveStatus(file),
    }))
  }, [chunks, prDiff.data])

  const totals = useMemo(
    () =>
      files.reduce(
        (acc, file) => ({
          additions: acc.additions + file.additions,
          deletions: acc.deletions + file.deletions,
        }),
        { additions: 0, deletions: 0 }
      ),
    [files]
  )

  useEffect(() => {
    if (!selectedTreePath) return
    const target = files.find((file) => file.treePath === selectedTreePath)
    if (!target) return
    sectionRefs.current[target.filePath]?.scrollIntoView({
      block: "start",
      behavior: "smooth",
    })
  }, [selectedTreePath, files])

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => setCollapsed(false)}
        aria-label="Expand git panel"
        title="Expand git panel"
        className="fixed top-3 right-3 z-30 flex size-7 items-center justify-center rounded-md border border-border bg-background text-muted-foreground shadow-sm hover:bg-accent hover:text-foreground"
      >
        <SidebarSimpleIcon className="size-4" />
      </button>
    )
  }

  return (
    <aside
      className={cn(
        "relative flex shrink-0 flex-col bg-[var(--ui-bg)]",
        fullScreen ? "fixed inset-0 !w-full" : "h-full"
      )}
      style={fullScreen ? { zIndex: Z.MODAL } : { width }}
    >
      <div className="flex h-11 shrink-0 items-center gap-1 px-3">
        {(
          [
            ["git", "Git"],
            ["desktop", "Desktop"],
            ["terminal", "Terminal"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setTopTab(id)}
            className={cn(
              "rounded-md px-2.5 py-1 text-xs transition-colors",
              topTab === id
                ? "bg-[var(--ui-accent-bubble)] font-medium text-[var(--ui-text)]"
                : "text-[var(--ui-text-dim)] hover:bg-[var(--ui-panel-2)]"
            )}
          >
            {label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => {
            setFullScreen(false)
            setCollapsed(true)
          }}
          aria-label="Collapse git panel"
          title="Collapse git panel"
          className="ml-auto rounded-md p-1.5 text-[var(--ui-text-dim)] transition-colors hover:bg-[var(--ui-panel-2)] hover:text-[var(--ui-text)]"
        >
          <SidebarSimpleIcon className="size-4" />
        </button>
        <button
          type="button"
          onClick={() => setFullScreen((v) => !v)}
          aria-label={fullScreen ? "Exit full screen" : "Enter full screen"}
          className="rounded-md p-1.5 text-[var(--ui-text-dim)] transition-colors hover:bg-[var(--ui-panel-2)] hover:text-[var(--ui-text)]"
        >
          {fullScreen ? (
            <ArrowsInIcon className="size-4" />
          ) : (
            <ArrowsOutIcon className="size-4" />
          )}
        </button>
      </div>

      <div
        className={cn(
          "flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-[var(--ui-border)] bg-[var(--ui-surface)] shadow-sm",
          fullScreen ? "mx-3 mb-3" : "mr-4 mb-4 ml-1"
        )}
      >
      {topTab !== "git" ? (
        <div className="flex flex-1 items-center justify-center p-6 text-xs text-[var(--ui-text-dim)]">
          Coming Soon
        </div>
      ) : (
        <>
      {pr && (
        <div className="border-b border-[var(--ui-border)] px-4 py-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium text-[var(--ui-text)]">
                {pr.title} #{pr.number}
              </div>
              <div className="mt-1 flex items-center gap-2 text-[11px] text-[var(--ui-text-dim)]">
                <span className="inline-flex items-center gap-1 rounded border border-[var(--ui-border)] px-1.5 py-0.5 capitalize">
                  <GitPullRequestIcon className="size-3" />
                  {pr.state}
                </span>
                <span>
                  {pr.headRef} → {pr.baseRef}
                </span>
              </div>
            </div>
            {pr.url && (
              <a
                href={pr.url}
                target="_blank"
                rel="noreferrer"
                className={buttonVariants({ variant: "outline", size: "sm" })}
              >
                <ArrowSquareOutIcon className="size-3" />
                View PR
              </a>
            )}
          </div>
        </div>
      )}

      <div className="flex items-center gap-1 border-b border-[var(--ui-border)] px-3 py-2">
        {(
          [
            ["diff", "Diff"],
            ["review", "Review"],
            ["commits", "Commits"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={cn(
              "rounded-md px-2.5 py-1 text-xs transition-colors",
              tab === id
                ? "bg-[var(--ui-accent-bubble)] font-medium text-[var(--ui-text)]"
                : "text-[var(--ui-text-dim)] hover:bg-[var(--ui-panel-2)]"
            )}
          >
            {label}
          </button>
        ))}
        {files.length > 0 && (
          <span className="ml-auto flex items-center gap-2 text-[11px] text-[var(--ui-text-dim)]">
            <span>
              {files.length} file{files.length === 1 ? "" : "s"}
            </span>
            <span className="text-[var(--ui-success)]">
              +{totals.additions}
            </span>
            <span className="text-[var(--ui-danger)]">-{totals.deletions}</span>
          </span>
        )}
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="min-h-0 flex-1 overflow-y-auto">
          {tab === "diff" && files.length > 0 ? (
            <div className="space-y-2 p-2">
              {files.map((file) => (
                <FileDiffSection
                  key={file.filePath}
                  file={file}
                  sectionRef={(node) => {
                    sectionRefs.current[file.filePath] = node
                  }}
                />
              ))}
            </div>
          ) : (
            <div className="p-6 text-center text-xs text-[var(--ui-text-dim)]">
              {tab !== "diff"
                ? "Coming Soon"
                : prDiff.isLoading
                  ? "Loading PR diff…"
                  : "No diff available."}
            </div>
          )}
        </div>

        {fullScreen && files.length > 0 && (
          <div className="w-72 shrink-0 border-l border-[var(--ui-border)] bg-[var(--ui-surface)]">
            <FileTreeExplorer
              files={files}
              selectedTreePath={selectedTreePath}
              onSelect={setSelectedTreePath}
            />
          </div>
        )}
      </div>
        </>
      )}
      </div>
      {!fullScreen && <PanelResizeHandle width={width} onResize={setWidth} />}
    </aside>
  )
}

function FileDiffSection({
  file,
  sectionRef,
}: {
  file: PanelFile
  sectionRef: (node: HTMLDivElement | null) => void
}) {
  const [open, setOpen] = useState(true)
  const diffOptions = useDiffOptions()

  return (
    <div
      ref={sectionRef}
      className="mb-2 scroll-mt-2 overflow-hidden rounded-lg border border-[var(--ui-border)]"
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 bg-[var(--ui-panel-2)] px-3 py-2 text-left text-xs"
      >
        <CaretDownIcon
          className={cn("size-3 transition-transform", !open && "-rotate-90")}
        />
        <span className="truncate font-medium text-[var(--ui-text)]">
          {file.treePath}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-2">
          <span className="text-[var(--ui-success)]">+{file.additions}</span>
          <span className="text-[var(--ui-danger)]">-{file.deletions}</span>
        </span>
      </button>
      {open &&
        (file.unrenderable ? (
          <div className="bg-[var(--ui-panel)] p-4 text-center text-xs text-[var(--ui-text-dim)]">
            Binary or large file — diff not shown.
          </div>
        ) : (
          <div className="max-h-[420px] overflow-auto bg-[var(--ui-panel)] p-2 font-mono text-[11px] leading-5">
            <MultiFileDiff
              oldFile={{ name: file.treePath, contents: file.originalContent }}
              newFile={{ name: file.treePath, contents: file.modifiedContent }}
              options={diffOptions}
            />
          </div>
        ))}
    </div>
  )
}

function FileTreeExplorer({
  files,
  selectedTreePath,
  onSelect,
}: {
  files: Array<PanelFile>
  selectedTreePath: string | null
  onSelect: (path: string) => void
}) {
  const paths = useMemo(() => files.map((file) => file.treePath), [files])
  const gitStatus = useMemo<Array<GitStatusEntry>>(
    () => files.map((file) => ({ path: file.treePath, status: file.status })),
    [files]
  )

  const { model } = useFileTree({
    paths,
    gitStatus,
    initialExpansion: "open",
    flattenEmptyDirectories: true,
    search: true,
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
    if (selectedTreePath) {
      model.scrollToPath(selectedTreePath, { focus: false })
    }
  }, [model, selectedTreePath])

  return (
    <div className="flex h-full flex-col">
      <FileTree model={model} style={{ height: "100%", ...treeThemeStyle() }} />
    </div>
  )
}
