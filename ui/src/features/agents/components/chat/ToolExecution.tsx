import { CaretDownIcon } from "@langchain/macaw-components/icons"
import {
  memo,
  useCallback,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { DiffView } from "./DiffView"
import { SqlResultTable, parseSqlResult } from "./SqlResultTable"
import { formatToolDisplay } from "./toolExecutionDisplay"
import { ScopedFileDiff } from "@/features/agents/components/ScopedFileDiff"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"
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
  const toggle = useCallback(() => setExpanded((prev) => !prev), [])
  const diffOptions = useDiffOptions()
  const inlineDiffOptions = useMemo(
    () => ({ ...diffOptions, disableFileHeader: true }),
    [diffOptions]
  )
  const scrollRef = useRef<HTMLDivElement>(null)
  const [scrolledFromTop, setScrolledFromTop] = useState(false)
  const [scrolledFromBottom, setScrolledFromBottom] = useState(false)

  const updateScrollIndicators = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    setScrolledFromTop(el.scrollTop > 0)
    setScrolledFromBottom(el.scrollTop < el.scrollHeight - el.clientHeight - 1)
  }, [])

  useLayoutEffect(() => {
    if (expanded) updateScrollIndicators()
  }, [expanded, updateScrollIndicators])

  const edgeShadows = [
    scrolledFromTop ? "inset 0 12px 10px -10px var(--shadow-color-subtle)" : "",
    scrolledFromBottom
      ? "inset 0 -12px 10px -10px var(--shadow-color-subtle)"
      : "",
  ]
    .filter(Boolean)
    .join(", ")

  const oldFile = { name: filePath, contents: originalContent }
  const newFile = { name: filePath, contents: newContent }

  if (!expanded) {
    return (
      <div className="my-0.5 text-xxs leading-5">
        <button
          type="button"
          onClick={toggle}
          className="inline-flex items-center gap-space-2 text-left transition-colors hover:brightness-125"
        >
          <span className={isError ? "text-error-secondary" : "text-secondary"}>
            Edited <span className="text-brand-primary">{fileName}</span>
          </span>
        </button>
      </div>
    )
  }

  return (
    <div className="my-space-1">
      <div className="my-0.5 mb-space-1 text-xxs leading-5">
        <button
          type="button"
          onClick={toggle}
          className="inline-flex items-center gap-space-2 text-left transition-colors hover:brightness-125"
        >
          <span className={isError ? "text-error-secondary" : "text-secondary"}>
            Edited file
          </span>
          <CaretDownIcon
            size={10}
            weight="fill"
            className="text-icon-tertiary"
            aria-hidden
          />
        </button>
      </div>

      <div className="overflow-hidden rounded-lg border border-subtle bg-surface-level-2">
        <div className="flex items-center gap-space-2 px-space-3 py-space-2">
          <span
            className={cn(
              "min-w-0 flex-1 truncate text-xs",
              isError ? "text-error-secondary" : "text-brand-primary"
            )}
          >
            {filePath}
          </span>
          <span className="flex shrink-0 items-center gap-space-2 text-xxs">
            <span className="text-success-secondary">+{additions}</span>
            <span className="text-error-secondary">-{deletions}</span>
          </span>
        </div>

        <div
          ref={scrollRef}
          onScroll={updateScrollIndicators}
          className="max-h-[250px] overflow-auto border-t border-default"
          style={{ boxShadow: edgeShadows || "none" }}
        >
          <ScopedFileDiff
            oldFile={oldFile}
            newFile={newFile}
            options={inlineDiffOptions}
          />
        </div>
      </div>
    </div>
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
      <div className="my-space-1 text-xxs leading-5">
        <DiffView diffData={diffData} />
        <span className="text-tertiary">Waiting for approval...</span>
      </div>
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
      <div className="my-0.5 text-xxs leading-5">
        <span className="text-status-yellow">
          Editing {getFileName(path)}...
        </span>
      </div>
    )
  }

  if (toolKind === "sql" && status === "completed" && parseSqlResult(output)) {
    return <SqlResultTable output={output} />
  }

  const displayName = formatToolDisplay(title, toolKind, input, repoPath)
  const statusTextClass =
    status === "error"
      ? "text-status-red"
      : status === "in_progress" || status === "pending"
        ? "text-status-yellow"
        : "text-secondary"

  return (
    <div className="my-0.5 text-xxs leading-5">
      <div className="flex min-w-0 items-center gap-space-2">
        <span className={cn(statusTextClass, "truncate")}>{displayName}</span>
        {status === "error" && output && (
          <span className="truncate text-error-tertiary">
            {output.slice(0, 80)}
          </span>
        )}
      </div>
    </div>
  )
})
