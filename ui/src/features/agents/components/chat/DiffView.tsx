import { useMemo } from "react"
import { MultiFileDiff } from "@pierre/diffs/react"
import { Box, Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
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
      <Box render={<p />} className="mt-2 font-mono text-meta text-ink-subtle">
        Binary file - diff not available
      </Box>
    )
  }

  if (stats.additions === 0 && stats.deletions === 0) {
    return (
      <Box render={<p />} className="mt-2 font-mono text-meta text-ink-subtle">
        No changes
      </Box>
    )
  }

  return (
    <Stack gap="xs" className="mt-2 font-mono text-label">
      <Inline gap="sm" align="center" className="text-meta text-ink-subtle">
        <Box render={<span />} className="min-w-0 truncate">
          {displayPath}
        </Box>
        {diffData.isNewFile && !snippet && <span>(new)</span>}
        <Box render={<span />} className="text-positive tabular-nums">
          +{stats.additions}
        </Box>
        <Box render={<span />} className="text-risk tabular-nums">
          -{stats.deletions}
        </Box>
      </Inline>
      <Box
        bg="panel"
        border="line"
        radius="compact"
        className="max-h-60 overflow-auto"
      >
        <MultiFileDiff
          oldFile={{ name: displayPath, contents: originalContent ?? "" }}
          newFile={{ name: displayPath, contents: newContent }}
          options={options}
        />
      </Box>
    </Stack>
  )
}
