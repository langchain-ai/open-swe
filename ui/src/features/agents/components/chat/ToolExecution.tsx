import { memo, useMemo, useState } from "react"
import { MultiFileDiff } from "@pierre/diffs/react"
import { DiffView } from "./DiffView"
import { SqlResultTable, parseSqlResult } from "./SqlResultTable"
import { formatToolDisplay } from "./toolExecutionDisplay"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"
import { Box, Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import {
  Collapsible,
  CollapsibleChevron,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@langchain/gtm-platform-design-system/ui/collapsible"
import { useDiffOptions } from "@/features/agents/utils/diffUtils"
import { countLineChanges } from "@/features/agents/utils/diffStats"
import { cn } from "@/lib/utils"

interface ToolExecutionProps {
  chunk: ToolExecutionChunk
  repoPath?: string
  onApprove?: (approvalRequestId: string) => void
  onReject?: (approvalRequestId: string) => void
  onAutoApprove?: (approvalRequestId: string) => void
}

function stripRepoPath(path: string, repoPath?: string): string {
  if (!repoPath || !path.startsWith(repoPath)) return path
  const relative = path.slice(repoPath.length)
  return relative.startsWith("/") ? "." + relative : "./" + relative
}

function getFileName(path: string): string {
  const normalized = path.replace(/\\/g, "/")
  const parts = normalized.split("/").filter(Boolean)
  return parts[parts.length - 1] || path
}

const ROW_CLASS = "min-h-5 text-label"

const InlineDiffCollapsible = memo(function InlineDiffCollapsible({
  filePath,
  fileName,
  originalContent,
  newContent,
  additions,
  deletions,
  isError,
}: {
  filePath: string
  fileName: string
  originalContent: string
  newContent: string
  additions: number
  deletions: number
  isError: boolean
}) {
  const [expanded, setExpanded] = useState(false)
  const diffOptions = useDiffOptions()
  const inlineDiffOptions = useMemo(
    () => ({ ...diffOptions, disableFileHeader: true }),
    [diffOptions]
  )

  const oldFile = { name: filePath, contents: originalContent }
  const newFile = { name: filePath, contents: newContent }

  return (
    <Collapsible open={expanded} onOpenChange={setExpanded}>
      <CollapsibleTrigger
        className={cn(
          ROW_CLASS,
          "cursor-pointer hover:text-ink",
          isError ? "text-risk" : "text-ink-subtle"
        )}
      >
        <CollapsibleChevron />
        {expanded ? (
          "Edited file"
        ) : (
          <span>
            Edited <span className="font-medium text-ink">{fileName}</span>
          </span>
        )}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <Box
          bg="muted"
          border="line"
          radius="compact"
          className="mt-1.5 overflow-hidden"
        >
          <Inline gap="sm" align="center" className="px-3 py-2">
            <Box
              render={<span />}
              className={cn(
                "min-w-0 flex-1 truncate font-mono text-label",
                isError ? "text-risk" : "text-ink"
              )}
            >
              {filePath}
            </Box>
            <Inline
              gap="sm"
              className="shrink-0 font-mono text-label tabular-nums"
            >
              <span className="text-positive">+{additions}</span>
              <span className="text-risk">-{deletions}</span>
            </Inline>
          </Inline>
          <Box className="max-h-60 overflow-auto border-t border-line">
            <MultiFileDiff
              oldFile={oldFile}
              newFile={newFile}
              options={inlineDiffOptions}
            />
          </Box>
        </Box>
      </CollapsibleContent>
    </Collapsible>
  )
})

export const ToolExecution = memo(function ToolExecution({
  chunk,
  repoPath,
}: ToolExecutionProps) {
  const { title, toolKind, input, status, output } = chunk
  const diffs = chunk.diffs?.length
    ? chunk.diffs
    : chunk.diffData
      ? [chunk.diffData]
      : []
  const diffData = diffs[diffs.length - 1]

  const isEditOp =
    toolKind === "edit" ||
    toolKind === "delete" ||
    toolKind === "move" ||
    diffData != null
  const isCompletedEditOp =
    isEditOp && diffData && (status === "completed" || status === "error")
  const editedFilePath = diffData
    ? stripRepoPath(diffData.filePath, repoPath)
    : ""
  const editedFileName = editedFilePath ? getFileName(editedFilePath) : ""
  const diffStats = diffData
    ? countLineChanges(
        diffData.originalContent,
        diffData.newContent,
        diffData.filePath
      )
    : null

  if (isCompletedEditOp && diffStats) {
    return (
      <InlineDiffCollapsible
        filePath={editedFilePath || diffData.filePath}
        fileName={editedFileName || editedFilePath || diffData.filePath}
        originalContent={diffData.originalContent ?? ""}
        newContent={diffData.newContent}
        additions={diffStats.additions}
        deletions={diffStats.deletions}
        isError={status === "error"}
      />
    )
  }

  if (isEditOp && status === "pending" && diffData) {
    return (
      <Stack gap="xs" className="text-label">
        <DiffView diffData={diffData} />
        <span className="text-ink-subtle">Waiting for approval...</span>
      </Stack>
    )
  }

  if (isEditOp && status === "in_progress") {
    const path = stripRepoPath(
      diffData?.filePath ||
        (input?.filePath as string) ||
        (input?.path as string) ||
        "file",
      repoPath
    )
    return (
      <Box className={ROW_CLASS}>
        <span className="shimmer-text">Editing {getFileName(path)}...</span>
      </Box>
    )
  }

  if (toolKind === "sql" && status === "completed" && parseSqlResult(output)) {
    return <SqlResultTable output={output} />
  }

  const displayName = formatToolDisplay(title, toolKind, input, repoPath)
  const statusTextClass =
    status === "error"
      ? "text-risk"
      : status === "in_progress" || status === "pending"
        ? "shimmer-text"
        : "text-ink-subtle"

  return (
    <Inline gap="sm" align="center" className={cn(ROW_CLASS, "min-w-0")}>
      <span className={cn("truncate", statusTextClass)}>{displayName}</span>
      {status === "error" && output && (
        <span className="truncate text-risk">{output.slice(0, 80)}</span>
      )}
    </Inline>
  )
})
