import { useEffect, useMemo, useRef, useState } from "react"
import { MultiFileDiff } from "@pierre/diffs/react"
import {
  FileTree,
  useFileTree,
  useFileTreeSelection,
} from "@pierre/trees/react"
import {
  ArrowsInIcon,
  ArrowsOutIcon,
  CaretDownIcon,
  GitPullRequestIcon,
} from "@phosphor-icons/react"
import type { GitStatus, GitStatusEntry } from "@pierre/trees"

import type { AgentThread } from "@/lib/agents/types"
import type { ChangedFileSummaryItem } from "@/components/agents/ported"
import { diffOptions } from "@/components/agents/utils/diffUtils"
import { summarizeChangedFiles } from "@/components/agents/ported"
import { Z } from "@/components/agents/z-index"
import { cn } from "@/lib/utils"

interface AgentGitPanelProps {
  thread: AgentThread
}

interface PanelFile {
  filePath: string
  treePath: string
  additions: number
  deletions: number
  originalContent: string
  modifiedContent: string
  status: GitStatus
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

export function AgentGitPanel({ thread }: AgentGitPanelProps) {
  const [tab, setTab] = useState<"diff" | "review" | "commits">("diff")
  const [fullScreen, setFullScreen] = useState(false)
  const [selectedTreePath, setSelectedTreePath] = useState<string | null>(null)
  const sectionRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const pr = thread.pr

  const chunks = useMemo(
    () => thread.messages.flatMap((message) => message.chunks),
    [thread.messages]
  )

  const files = useMemo<Array<PanelFile>>(() => {
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
  }, [chunks])

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

  return (
    <aside
      className={cn(
        "flex shrink-0 flex-col border-l border-[var(--ui-border)] bg-[var(--ui-surface)]",
        fullScreen ? "fixed inset-0 w-full" : "h-full w-[420px]"
      )}
      style={fullScreen ? { zIndex: Z.MODAL } : undefined}
    >
      <div className="flex h-11 shrink-0 items-center gap-1 border-b border-[var(--ui-border)] px-3">
        {(["Git", "Desktop", "Terminal"] as const).map((label, i) => (
          <button
            key={label}
            type="button"
            className={cn(
              "rounded-md px-2.5 py-1 text-xs transition-colors",
              i === 0
                ? "bg-[var(--ui-accent-bubble)] font-medium text-[var(--ui-text)]"
                : "text-[var(--ui-text-dim)] hover:bg-[var(--ui-panel-2)]"
            )}
          >
            {label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setFullScreen((v) => !v)}
          aria-label={fullScreen ? "Exit full screen" : "Enter full screen"}
          className="ml-auto rounded-md p-1.5 text-[var(--ui-text-dim)] transition-colors hover:bg-[var(--ui-panel-2)] hover:text-[var(--ui-text)]"
        >
          {fullScreen ? (
            <ArrowsInIcon className="size-4" />
          ) : (
            <ArrowsOutIcon className="size-4" />
          )}
        </button>
      </div>

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
              {tab === "commits"
                ? "Commit history will appear here."
                : tab === "review"
                  ? "Review comments will appear here."
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
      {open && (
        <div className="max-h-[420px] overflow-auto bg-[var(--ui-panel)] p-2 font-mono text-[11px] leading-5">
          <MultiFileDiff
            oldFile={{ name: file.treePath, contents: file.originalContent }}
            newFile={{ name: file.treePath, contents: file.modifiedContent }}
            options={diffOptions}
          />
        </div>
      )}
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
