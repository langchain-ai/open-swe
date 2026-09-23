import { useEffect, useMemo, useRef } from "react"
import { ArrowClockwiseIcon } from "@phosphor-icons/react"
import { FileTree, useFileTree, useFileTreeSearch } from "@pierre/trees/react"
import type { FileTreeBatchOperation } from "@pierre/trees"

import type { TerminalTarget } from "@/features/agents/lib/terminalSession"
import type { WorkspaceEntry } from "@/features/agents/lib/workspaceFiles"
import {
  TREE_UNSAFE_CSS,
  treeThemeStyle,
} from "@/features/agents/components/DiffFilesView"
import { useDirectoryEntries } from "@/features/agents/lib/workspaceFiles"
import { cn } from "@/lib/utils"

interface FileBrowserPanelProps {
  target: TerminalTarget
  /** Entry open in the surface; revealed and selected in the tree. */
  selectedPath: string | null
  /** Bumped when the same path should be revealed again. */
  selectedPathRevealId: number
  onOpenFile: (relativePath: string) => void
  onRefreshSelectedFile?: () => void
}

function treePath(entry: WorkspaceEntry): string {
  return entry.kind === "directory" ? `${entry.path}/` : entry.path
}

function pathDepth(path: string): number {
  return path.split("/").filter(Boolean).length
}

function byDepth(paths: Array<string>): Array<string> {
  return paths.sort((left, right) => pathDepth(left) - pathDepth(right))
}

/** The add/remove batch that turns one loaded path set into the next. */
function buildFileTreePathUpdates(
  previousPaths: ReadonlyArray<string>,
  nextPaths: ReadonlyArray<string>
): Array<FileTreeBatchOperation> {
  const previous = new Set(previousPaths)
  const next = new Set(nextPaths)
  const removedRoots: Array<string> = []
  const updates: Array<FileTreeBatchOperation> = []
  for (const path of byDepth(previousPaths.filter((p) => !next.has(p)))) {
    if (removedRoots.some((root) => path.startsWith(root))) continue
    const recursive = path.endsWith("/")
    updates.push({ type: "remove", path, ...(recursive ? { recursive } : {}) })
    if (recursive) removedRoots.push(path)
  }
  for (const path of byDepth(nextPaths.filter((p) => !previous.has(p))))
    updates.push({ type: "add", path })
  return updates
}

function ancestors(path: string): Array<string> {
  const segments = path.split("/")
  return segments.slice(0, -1).map((_, index) => {
    return segments.slice(0, index + 1).join("/")
  })
}

export function FileBrowserPanel({
  target,
  selectedPath,
  selectedPathRevealId,
  onOpenFile,
  onRefreshSelectedFile,
}: FileBrowserPanelProps) {
  const {
    entries,
    load,
    loadIndex,
    clearIndex,
    refresh,
    ready,
    error,
    isPending,
  } = useDirectoryEntries(target)
  const entryKinds = useMemo(
    () => new Map(entries.map((entry) => [entry.path, entry.kind] as const)),
    [entries]
  )
  const entryKindsRef = useRef(entryKinds)
  const treePaths = useMemo(() => entries.map(treePath), [entries])
  const directoryPaths = useMemo(
    () => entries.filter((entry) => entry.kind === "directory").map(treePath),
    [entries]
  )
  const previousTreePathsRef = useRef<ReadonlyArray<string> | null>(null)
  const syncingSelectionRef = useRef(false)
  const handledRevealRef = useRef<string | null>(null)

  const { model } = useFileTree({
    paths: [],
    density: "compact",
    fileTreeSearchMode: "hide-non-matches",
    flattenEmptyDirectories: true,
    initialExpansion: "closed",
    icons: "complete",
    search: false,
    unsafeCSS: TREE_UNSAFE_CSS,
    onSelectionChange: (selectedPaths) => {
      // Reveal-driven selection echoes an already open file.
      if (syncingSelectionRef.current) return
      const path = selectedPaths.at(-1)?.replace(/\/$/, "")
      if (path && entryKindsRef.current.get(path) === "file") onOpenFile(path)
    },
  })
  const search = useFileTreeSearch(model)

  // Load a folder's children the first time it is expanded.
  useEffect(() => {
    const loadExpanded = () => {
      if (model.isSearchOpen()) return
      for (const path of directoryPaths) {
        const item = model.getItem(path)
        if (item && "isExpanded" in item && item.isExpanded())
          void load(path.slice(0, -1))
      }
    }
    loadExpanded()
    return model.subscribe(loadExpanded)
  }, [directoryPaths, load, model])

  useEffect(() => {
    model.setGitStatus(
      entries
        .filter((entry) => entry.ignored)
        .map((entry) => ({ path: treePath(entry), status: "ignored" }))
    )
  }, [entries, model])

  useEffect(() => {
    if (!selectedPath) return
    for (const directory of ["", ...ancestors(selectedPath)])
      void load(directory)
  }, [load, selectedPath])

  useEffect(() => {
    if (!ready || previousTreePathsRef.current === treePaths) return
    entryKindsRef.current = entryKinds
    const previous = previousTreePathsRef.current
    previousTreePathsRef.current = treePaths
    if (previous === null) {
      model.resetPaths(treePaths)
      return
    }
    const updates = buildFileTreePathUpdates(previous, treePaths)
    if (updates.length > 0) model.batch(updates)
  }, [ready, entryKinds, model, treePaths])

  // Reveal files opened from outside the tree (links, terminal paths).
  useEffect(() => {
    if (!selectedPath || entryKinds.get(selectedPath) !== "file") return
    const revealKey = `${selectedPath}:${selectedPathRevealId}`
    if (handledRevealRef.current === revealKey) return
    const item = model.getItem(selectedPath)
    if (!item) return
    handledRevealRef.current = revealKey
    if (model.getSelectedPaths().includes(selectedPath)) return
    syncingSelectionRef.current = true
    model.closeSearch()
    for (const path of model.getSelectedPaths()) model.getItem(path)?.deselect()
    for (const directory of ancestors(selectedPath)) {
      const ancestor = model.getItem(`${directory}/`)
      if (ancestor && "expand" in ancestor) ancestor.expand()
    }
    item.select()
    model.scrollToPath(selectedPath, { focus: true, offset: "center" })
    queueMicrotask(() => {
      syncingSelectionRef.current = false
    })
  }, [entryKinds, model, selectedPath, selectedPathRevealId])

  // Search filters the tree's paths, so give it every file while it is open.
  const searchOpen = search.isOpen
  useEffect(() => {
    if (searchOpen) void loadIndex()
    else clearIndex()
  }, [clearIndex, loadIndex, searchOpen])

  const handleSearchChange = (value: string) => {
    if (value.trim()) search.setValue(value)
    else search.close()
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-background">
      <div className="flex h-9 shrink-0 items-center gap-1 border-b border-border px-2">
        <button
          type="button"
          aria-label="Refresh files"
          title="Refresh files"
          onClick={() => {
            refresh()
            onRefreshSelectedFile?.()
          }}
          className="flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground/70 transition-colors hover:text-foreground"
        >
          <ArrowClockwiseIcon
            className={cn("size-3.5", isPending && "animate-spin")}
          />
        </button>
        <input
          type="search"
          value={search.value}
          aria-label="Search files"
          placeholder="Search files"
          spellCheck={false}
          onChange={(event) => handleSearchChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== "Escape") return
            search.close()
            event.currentTarget.blur()
          }}
          className="h-7 min-w-0 flex-1 bg-transparent px-1 text-xs outline-none placeholder:text-muted-foreground/70"
        />
      </div>
      {error ? (
        <button
          type="button"
          onClick={refresh}
          className="p-4 text-left text-xs leading-relaxed text-destructive"
        >
          {error} Click to retry.
        </button>
      ) : null}
      <FileTree
        model={model}
        aria-label="Workspace files"
        className="min-h-0 flex-1 overflow-hidden"
        style={{ height: "100%", ...treeThemeStyle() }}
      />
    </div>
  )
}
