import { useMemo } from "react"
import { MultiFileDiff } from "@pierre/diffs/react"
import type { DiffData } from "@/features/agents/lib/types"
import { useDiffOptions } from "@/features/agents/utils/diffUtils"
import { countLineChanges } from "@/features/agents/utils/diffStats"

interface DiffViewProps {
  diffData: DiffData
  snippet?: boolean
}

export function DiffView({ diffData, snippet = false }: DiffViewProps) {
  const diffOptions = useDiffOptions()
  const options = useMemo(
    () =>
      snippet ? { ...diffOptions, disableLineNumbers: true } : diffOptions,
    [diffOptions, snippet]
  )
  const { originalContent, newContent, filePath, isBinary } = diffData
  const displayPath = filePath.split("/").pop() || filePath
  const stats = useMemo(
    () => countLineChanges(originalContent, newContent, filePath),
    [filePath, newContent, originalContent]
  )

  if (isBinary) {
    return (
      <div className="mt-2 font-mono text-meta text-ink-subtle">
        Binary file - diff not available
      </div>
    )
  }

  if (stats.additions === 0 && stats.deletions === 0) {
    return (
      <div className="mt-2 font-mono text-meta text-ink-subtle">No changes</div>
    )
  }

  return (
    <div className="mt-2 font-mono text-label">
      <div className="mb-1 flex items-center gap-2 text-ink-subtle">
        <span className="text-ink-subtle">{displayPath}</span>
        {diffData.isNewFile && !snippet && <span>(new)</span>}
        <span className="text-positive">+{stats.additions}</span>
        <span className="text-risk">-{stats.deletions}</span>
      </div>
      <div className="max-h-60 overflow-auto rounded-compact border border-line/60 bg-panel">
        <MultiFileDiff
          oldFile={{ name: displayPath, contents: originalContent ?? "" }}
          newFile={{ name: displayPath, contents: newContent }}
          options={options}
        />
      </div>
    </div>
  )
}
