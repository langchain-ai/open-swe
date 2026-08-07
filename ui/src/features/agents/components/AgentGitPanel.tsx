import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"
import { useQueryClient } from "@tanstack/react-query"
import {
  MultiFileDiff,
  Virtualizer,
  WorkerPoolContextProvider,
} from "@pierre/diffs/react"
import {
  FileTree,
  useFileTree,
  useFileTreeSelection,
} from "@pierre/trees/react"
import { CaretDownIcon } from "@phosphor-icons/react"
import type { FileContents } from "@pierre/diffs/react"
import type { GitStatus, GitStatusEntry } from "@pierre/trees"

import type { AgentThread } from "@/features/agents/lib/types"
import type { ThreadPrDiffFile } from "@/features/agents/lib/api"
import { agentsApi } from "@/features/agents/lib/api"
import {
  agentThreadKeys,
  invalidateAgentThreadLists,
  useAgentThreadPrDiff,
  useAgentThreadTurnDiff,
} from "@/features/agents/lib/queries"
import { ReviewTab } from "@/features/reviews/components/ReviewTab"
import { PrHeader } from "@/features/reviews/components/PrHeader"
import { buttonVariants } from "@/components/ui/button"
import { AgentPanelShell } from "@/features/agents/components/AgentPanelShell"
import { DiffWrapToggle } from "@/features/agents/components/DiffWrapToggle"
import { PlanView } from "@/features/agents/components/PlanView"
import {
  DIFF_VIRTUALIZER_CONFIG,
  DIFF_VIRTUAL_METRICS,
  DIFF_WORKER_HIGHLIGHTER_OPTIONS,
  DIFF_WORKER_POOL_OPTIONS,
  fileContentsCacheKey,
  useDiffOptions,
} from "@/features/agents/utils/diffUtils"
import { useIsMobile } from "@/lib/useIsMobile"
import { cn } from "@/lib/utils"

export type AgentPanelTab = "git" | "plan"

interface AgentGitPanelProps {
  thread: AgentThread
  /** Path to select and scroll to, set when a transcript row is clicked. */
  revealFilePath?: string | null
  collapsed: boolean
  requestedTab: AgentPanelTab
  onCollapsedChange: (next: boolean) => void
  onTabChange: (tab: AgentPanelTab) => void
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

// Neutral filename foreground from the pierre Shiki themes (pierre-light /
// pierre-dark sidebar foreground). The tree tints filename text by git status,
// so feeding this keeps names neutral grey/white instead of accent-blue.
const TREE_FILE_FG = "light-dark(#525252, #a3a3a3)"

// Selected rows must read as high-contrast (white in dark, near-black in light)
// while the rest stay neutral. The built-in git-status content color outranks
// the selection color by specificity, so override it from the `unsafe` layer.
export const TREE_UNSAFE_CSS = `
  [data-item-selected="true"] [data-item-section="content"] {
    color: var(--trees-selected-fg);
  }

  /* On click a row is focus-ringed a frame before it's marked selected, which
   * flashes the accent outline. Pointer focus doesn't match :focus-visible, so
   * drop the ring there; keyboard navigation keeps it. */
  [data-item-focused="true"]:not(:focus-visible)::before {
    outline-color: transparent;
  }
`

export function treeThemeStyle(): React.CSSProperties {
  return {
    "--trees-theme-sidebar-bg": "var(--card)",
    "--trees-theme-sidebar-fg": "var(--foreground)",
    "--trees-theme-sidebar-border": "var(--border)",
    "--trees-theme-sidebar-header-fg": "var(--muted-foreground)",
    "--trees-theme-list-hover-bg":
      "color-mix(in oklab, var(--primary) 10%, transparent)",
    "--trees-theme-list-active-selection-bg":
      "color-mix(in oklab, var(--primary) 22%, transparent)",
    "--trees-theme-list-active-selection-fg": "var(--foreground)",
    "--trees-selected-focused-border-color-override": "transparent",
    "--trees-theme-input-bg": "var(--card)",
    "--trees-theme-input-fg": "var(--foreground)",
    "--trees-theme-input-border": "var(--border)",
    "--trees-theme-focus-ring": "var(--primary)",
    "--trees-theme-scrollbar-thumb": "var(--border)",
    "--trees-theme-git-added-fg": TREE_FILE_FG,
    "--trees-theme-git-modified-fg": TREE_FILE_FG,
    "--trees-theme-git-deleted-fg": TREE_FILE_FG,
    "--trees-theme-git-renamed-fg": TREE_FILE_FG,
    "--trees-theme-git-untracked-fg": TREE_FILE_FG,
    "--trees-theme-git-ignored-fg": "var(--muted-foreground)",
  } as React.CSSProperties
}

export function AgentGitPanel({
  thread,
  revealFilePath,
  collapsed,
  requestedTab,
  onCollapsedChange,
  onTabChange,
}: AgentGitPanelProps) {
  const queryClient = useQueryClient()
  const stream = useAgentThreadStream()
  const [tab, setTab] = useState<"diff" | "review" | "commits">("diff")
  const isMobile = useIsMobile()
  const hasPlan = Boolean(
    thread.planStatus &&
    thread.planStatus !== "approved" &&
    thread.planStatus !== "cancelled"
  )

  const topTab = hasPlan || requestedTab !== "plan" ? requestedTab : "git"
  const onPlanApproved = useCallback(
    (runId: string) => {
      queryClient.setQueryData<AgentThread>(
        agentThreadKeys.detail(thread.id),
        (current) =>
          current
            ? { ...current, planStatus: "approved", status: "running" }
            : current
      )
      void queryClient.invalidateQueries({ queryKey: ["plan", thread.id] })
      invalidateAgentThreadLists(queryClient)
      onTabChange("git")
      void stream.client.runs.join(thread.id, runId).finally(() => {
        void queryClient.invalidateQueries({
          queryKey: agentThreadKeys.detail(thread.id),
        })
      })
    },
    [onTabChange, queryClient, stream, thread.id]
  )

  // Collapsed state is owned by the parent (so the plan banner can reserve space
  // for the floating expand button); persistence to localStorage lives there too.
  const setCollapsed = onCollapsedChange

  const [selectedTreePath, setSelectedTreePath] = useState<string | null>(null)
  const sectionRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const pr = thread.pr

  // The open/closed state is persisted to localStorage, so it carries across
  // threads and reloads. Still uncollapse when a PR lands mid-session.
  const [prSeen, setPrSeen] = useState<{ threadId: string; hadPr: boolean }>(
    () => ({ threadId: thread.id, hadPr: Boolean(pr) })
  )
  if (prSeen.threadId !== thread.id) {
    setPrSeen({ threadId: thread.id, hadPr: Boolean(pr) })
  } else if (pr && !prSeen.hadPr) {
    setPrSeen({ threadId: thread.id, hadPr: true })
    setCollapsed(false)
  }

  const prDiff = useAgentThreadPrDiff(thread.id, Boolean(pr))
  // Without a PR the sandbox's git checkpoints are the only source of truth for
  // what this thread changed.
  const turnDiff = useAgentThreadTurnDiff(thread.id, null, !pr && !collapsed)
  const [recoveringPatch, setRecoveringPatch] = useState(false)
  const [recoveryError, setRecoveryError] = useState<string | null>(null)
  const canDownloadRecovery =
    thread.status !== "running" && thread.isOwner !== false

  const downloadRecoveryPatch = useCallback(async () => {
    setRecoveringPatch(true)
    setRecoveryError(null)
    try {
      const { blob, filename } = await agentsApi.downloadThreadRecoveryPatch(
        thread.id
      )
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)
    } catch (error) {
      setRecoveryError(
        error instanceof Error ? error.message : "Failed to download patch"
      )
    } finally {
      setRecoveringPatch(false)
    }
  }, [thread.id])

  const files = useMemo<Array<PanelFile>>(() => {
    const diffFiles = prDiff.data?.files ?? turnDiff.data?.files ?? []
    const prefix = commonDirPrefix(diffFiles.map((file) => file.path))
    return diffFiles.map((file) => ({
      filePath: file.path,
      treePath:
        prefix && file.path.startsWith(prefix)
          ? file.path.slice(prefix.length)
          : file.path,
      additions: file.additions,
      deletions: file.deletions,
      originalContent: file.originalContent ?? "",
      modifiedContent: file.modifiedContent ?? "",
      status: prFileStatus(file),
      unrenderable: file.unrenderable,
    }))
  }, [prDiff.data, turnDiff.data])

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

  const filesRef = useRef(files)
  filesRef.current = files
  const selectTreePath = useCallback((path: string) => {
    setSelectedTreePath(path)
    const target = filesRef.current.find((file) => file.treePath === path)
    if (!target) return
    sectionRefs.current[target.filePath]?.scrollIntoView({
      block: "start",
      behavior: "smooth",
    })
  }, [])

  useEffect(() => {
    if (!revealFilePath) return
    // Transcript rows carry sandbox-absolute paths; diff files are repo-relative.
    const target = filesRef.current.find(
      (file) =>
        file.filePath === revealFilePath ||
        revealFilePath.endsWith(`/${file.filePath}`)
    )
    if (target) selectTreePath(target.treePath)
  }, [revealFilePath, files, selectTreePath])

  return (
    <AgentPanelShell
      tabs={[["git", "Git"], ...(hasPlan ? ([["plan", "Plan"]] as const) : [])]}
      activeTab={topTab}
      onTabChange={onTabChange}
      collapsed={collapsed}
      onCollapsedChange={setCollapsed}
    >
      {({ fullScreen }) => (
        <>
          {topTab === "plan" ? (
            <PlanView threadId={thread.id} onApprove={onPlanApproved} />
          ) : topTab !== "git" ? (
            <div className="flex flex-1 items-center justify-center p-6 text-xs text-muted-foreground/70">
              Coming Soon
            </div>
          ) : (
            <>
              {pr && (
                <PrHeader
                  className="border-b border-border px-4 py-3"
                  url={pr.url}
                  title={pr.title}
                  number={pr.number}
                  state={pr.state}
                  headRef={pr.headRef}
                  baseRef={pr.baseRef}
                  titleClassName="truncate text-sm"
                />
              )}

              <div className="flex items-center gap-1 border-b border-border px-3 py-2">
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
                        ? "bg-accent font-medium text-foreground"
                        : "text-muted-foreground/70 hover:bg-accent"
                    )}
                  >
                    {label}
                  </button>
                ))}
                <div className="ml-auto flex min-w-0 items-center gap-2">
                  {tab === "diff" && <DiffWrapToggle />}
                  {recoveryError && (
                    <span
                      title={recoveryError}
                      className="max-w-40 truncate text-[11px] text-destructive"
                    >
                      {recoveryError}
                    </span>
                  )}
                  {canDownloadRecovery && (
                    <button
                      type="button"
                      onClick={downloadRecoveryPatch}
                      disabled={recoveringPatch}
                      className={cn(
                        buttonVariants({ variant: "outline", size: "sm" }),
                        "h-7 px-2 text-[11px]"
                      )}
                    >
                      {recoveringPatch ? "Preparing…" : "Download patch"}
                    </button>
                  )}
                  {files.length > 0 && (
                    <span className="flex items-center gap-2 text-[11px] text-muted-foreground/70">
                      <span>
                        {files.length} file{files.length === 1 ? "" : "s"}
                      </span>
                      <span className="text-success-foreground">
                        +{totals.additions}
                      </span>
                      <span className="text-destructive">
                        -{totals.deletions}
                      </span>
                    </span>
                  )}
                </div>
              </div>

              <div className="flex min-h-0 flex-1">
                {tab === "review" ? (
                  <ReviewTab thread={thread} />
                ) : tab === "diff" && files.length > 0 ? (
                  <WorkerPoolContextProvider
                    poolOptions={DIFF_WORKER_POOL_OPTIONS}
                    highlighterOptions={DIFF_WORKER_HIGHLIGHTER_OPTIONS}
                  >
                    <Virtualizer
                      className="min-h-0 flex-1 overflow-y-auto"
                      contentClassName="space-y-2 p-2"
                      config={DIFF_VIRTUALIZER_CONFIG}
                    >
                      {files.map((file) => (
                        <FileDiffSection
                          key={file.filePath}
                          file={file}
                          sectionRef={(node) => {
                            sectionRefs.current[file.filePath] = node
                          }}
                        />
                      ))}
                    </Virtualizer>
                  </WorkerPoolContextProvider>
                ) : (
                  <div className="min-h-0 flex-1 overflow-y-auto p-6 text-center text-xs text-muted-foreground/70">
                    {tab !== "diff"
                      ? "Coming Soon"
                      : prDiff.isLoading
                        ? "Loading PR diff…"
                        : "No diff available."}
                  </div>
                )}

                {tab === "diff" &&
                  fullScreen &&
                  !isMobile &&
                  files.length > 0 && (
                    <div className="w-72 shrink-0 border-l border-border bg-card">
                      <FileTreeExplorer
                        files={files}
                        selectedTreePath={selectedTreePath}
                        onSelect={selectTreePath}
                      />
                    </div>
                  )}
              </div>
            </>
          )}
        </>
      )}
    </AgentPanelShell>
  )
}

const FileDiffSection = memo(
  function FileDiffSection({
    file,
    sectionRef,
  }: {
    file: PanelFile
    sectionRef: (node: HTMLDivElement | null) => void
  }) {
    const [open, setOpen] = useState(true)
    const diffOptions = useDiffOptions()
    const oldFile = useMemo<FileContents>(
      () => ({
        name: file.treePath,
        contents: file.originalContent,
        cacheKey: fileContentsCacheKey(
          file.filePath,
          "old",
          file.originalContent
        ),
      }),
      [file.filePath, file.originalContent, file.treePath]
    )
    const newFile = useMemo<FileContents>(
      () => ({
        name: file.treePath,
        contents: file.modifiedContent,
        cacheKey: fileContentsCacheKey(
          file.filePath,
          "new",
          file.modifiedContent
        ),
      }),
      [file.filePath, file.modifiedContent, file.treePath]
    )

    return (
      <div
        ref={sectionRef}
        className="mb-2 scroll-mt-2 overflow-hidden rounded-lg border border-border"
      >
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-center gap-2 bg-accent px-3 py-2 text-left text-xs"
        >
          <CaretDownIcon
            className={cn("size-3 transition-transform", !open && "-rotate-90")}
          />
          <span className="truncate font-medium text-foreground">
            {file.treePath}
          </span>
          <span className="ml-auto flex shrink-0 items-center gap-2">
            <span className="text-success-foreground">+{file.additions}</span>
            <span className="text-destructive">-{file.deletions}</span>
          </span>
        </button>
        {open &&
          (file.unrenderable ? (
            <div className="bg-card p-4 text-center text-xs text-muted-foreground/70">
              Binary or large file — diff not shown.
            </div>
          ) : (
            <div className="overflow-hidden bg-card p-2">
              <MultiFileDiff
                oldFile={oldFile}
                newFile={newFile}
                options={diffOptions}
                metrics={DIFF_VIRTUAL_METRICS}
              />
            </div>
          ))}
      </div>
    )
  },
  (prev, next) => prev.file === next.file
)

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
