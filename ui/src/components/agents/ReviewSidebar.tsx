import { createContext, useContext, useEffect, useMemo, useState } from "react"
import {
  FileTree,
  useFileTree,
  useFileTreeSelection,
} from "@pierre/trees/react"

import type {
  FileTreeDirectoryHandle,
  GitStatus,
  GitStatusEntry,
} from "@pierre/trees"
import type { ReviewDiffFile } from "@/lib/api"
import { Skeleton } from "@/components/ui/skeleton"
import { TREE_UNSAFE_CSS, treeThemeStyle } from "@/components/agents/AgentGitPanel"

function reviewFileGitStatus(status: ReviewDiffFile["status"]): GitStatus {
  if (status === "removed") return "deleted"
  if (status === "added") return "added"
  if (status === "renamed") return "renamed"
  return "modified"
}

export interface ReviewSidebarData {
  title: string
  files: Array<ReviewDiffFile> | null
  selected: string | null
  viewed: Set<string>
  onSelect: (path: string) => void
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

export function ReviewFileTree({ data }: { data: ReviewSidebarData }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col pb-2">
      <div className="px-4 py-1 text-[10px] font-semibold tracking-wide text-[var(--ui-text-dim)] uppercase">
        {data.title}
      </div>
      {!data.files ? (
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
