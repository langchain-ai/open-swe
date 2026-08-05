import { useMemo } from "react"
import { MultiFileDiff } from "@pierre/diffs/react"
import type { DiffData } from "@/features/agents/lib/types"
import { useDiffOptions } from "@/features/agents/utils/diffUtils"
import { countLineChanges } from "@/features/agents/utils/diffStats"

interface DiffViewProps {
  diffData: DiffData
}

export function DiffView({ diffData }: DiffViewProps) {
  const diffOptions = useDiffOptions()
  const { originalContent, newContent, filePath, isBinary } = diffData
  const displayPath = filePath.split("/").pop() || filePath
  const stats = useMemo(
    () => countLineChanges(originalContent, newContent, filePath),
    [filePath, newContent, originalContent]
  )

  if (isBinary) {
    return (
      <div className="mt-2 font-mono text-xs text-gray-500">
        Binary file - diff not available
      </div>
    )
  }

  if (stats.additions === 0 && stats.deletions === 0) {
    return (
      <div className="mt-2 font-mono text-xs text-gray-500">No changes</div>
    )
  }

  return (
    <div className="mt-2 font-mono text-xs">
      <div className="mb-1 flex items-center gap-2 text-gray-500">
        <span className="text-gray-400">{displayPath}</span>
        {diffData.isNewFile && <span>(new)</span>}
        <span className="text-green-400">+{stats.additions}</span>
        <span className="text-red-400">-{stats.deletions}</span>
      </div>
      <div className="max-h-60 overflow-auto rounded-lg border border-border/60 bg-card">
        <MultiFileDiff
          oldFile={{ name: displayPath, contents: originalContent ?? "" }}
          newFile={{ name: displayPath, contents: newContent }}
          options={diffOptions}
        />
      </div>
    </div>
  )
}
