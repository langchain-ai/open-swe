import { createContext, useContext, useEffect, useMemo, useState } from "react"

import type { ReviewDiffFile } from "@/lib/api"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

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
  const grouped = useMemo(() => {
    const byDir = new Map<string, Array<ReviewDiffFile>>()
    for (const file of data.files ?? []) {
      const idx = file.path.lastIndexOf("/")
      const dir = idx === -1 ? "" : file.path.slice(0, idx)
      const list = byDir.get(dir) ?? []
      list.push(file)
      byDir.set(dir, list)
    }
    return Array.from(byDir.entries()).sort(([a], [b]) => a.localeCompare(b))
  }, [data.files])

  return (
    <div className="min-h-0 flex-1 overflow-y-auto pb-2">
      <div className="px-4 py-1 text-[10px] font-semibold tracking-wide text-[var(--ui-text-dim)] uppercase">
        {data.title}
      </div>
      {!data.files ? (
        <div className="px-4 pt-1">
          <Skeleton className="h-40 w-full" />
        </div>
      ) : (
        grouped.map(([dir, dirFiles]) => (
          <div key={dir || "."} className="mb-1">
            {dir && (
              <div className="truncate px-4 py-1 text-[11px] text-[var(--ui-text-dim)]">
                {dir}
              </div>
            )}
            {dirFiles.map((file) => {
              const name = file.path.slice(dir ? dir.length + 1 : 0)
              return (
                <button
                  key={file.path}
                  type="button"
                  onClick={() => data.onSelect(file.path)}
                  className={cn(
                    "flex w-full items-center gap-2 px-4 py-1 text-left text-xs text-[var(--ui-text-muted)] transition-colors hover:bg-[var(--ui-sidebar-hover)]",
                    data.selected === file.path &&
                      "bg-[var(--ui-accent-bubble)] text-[var(--ui-text)]",
                    data.viewed.has(file.path) && "opacity-50"
                  )}
                >
                  <span
                    className={cn(
                      "truncate",
                      file.status === "added" && "text-emerald-500",
                      file.status === "deleted" && "text-red-500"
                    )}
                  >
                    {name}
                  </span>
                  <span className="ml-auto flex shrink-0 items-center gap-1.5 font-mono text-[10px]">
                    {file.additions > 0 && (
                      <span className="text-emerald-500">
                        +{file.additions}
                      </span>
                    )}
                    {file.deletions > 0 && (
                      <span className="text-red-500">-{file.deletions}</span>
                    )}
                  </span>
                </button>
              )
            })}
          </div>
        ))
      )}
    </div>
  )
}
