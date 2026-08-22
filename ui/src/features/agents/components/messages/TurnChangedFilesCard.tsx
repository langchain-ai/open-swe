import { memo, useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"

import type { ThreadPrDiffFile } from "@/features/agents/lib/api"
import { COMPOSER_PATH_DRAG_MIME } from "@/features/agents/components/composer/composerTrigger"
import { useAgentThreadRunDiff } from "@/features/agents/lib/queries"

const INLINE_CHANGED_FILES_LIMIT = 10

/** What a completed agent run changed, loaded from its persisted diff artifact. */
export const TurnChangedFilesCard = memo(
  function TurnChangedFilesCardComponent({
    threadId,
    turnKey,
    isLatestTurn,
    onOpenFile,
  }: {
    threadId: string
    turnKey: string
    isLatestTurn: boolean
    onOpenFile?: (filePath: string) => void
  }) {
    const [open, setOpen] = useState(isLatestTurn)
    const turnDiff = useAgentThreadRunDiff(threadId, turnKey, open, {
      maxFiles: INLINE_CHANGED_FILES_LIMIT,
      includeContent: false,
    })
    const files = turnDiff.data?.files ?? []
    const summary = turnDiff.data?.summary

    if (
      turnDiff.data?.status === "missing" ||
      (turnDiff.isFetched && files.length === 0)
    ) {
      return null
    }

    const totalFiles = summary?.files ?? files.length
    const additions = summary?.additions ?? 0
    const deletions = summary?.deletions ?? 0
    const omittedFiles = Math.max(0, totalFiles - files.length)

    return (
      <div
        data-testid="turn-changed-files-card"
        className="mt-3 overflow-hidden rounded-xl bg-muted/40"
      >
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          className="flex w-full items-center gap-2 px-3 py-2 text-xs text-muted-foreground transition-colors hover:bg-accent/30"
        >
          {open ? (
            <ChevronDown className="size-3.5" />
          ) : (
            <ChevronRight className="size-3.5" />
          )}
          {turnDiff.isPending && open ? (
            <span>Reading changed files…</span>
          ) : totalFiles > 0 ? (
            <>
              <span>
                {totalFiles} file{totalFiles === 1 ? "" : "s"} changed
              </span>
              <span className="text-success-foreground">+{additions}</span>
              <span className="text-destructive">-{deletions}</span>
            </>
          ) : (
            <span>Changed files</span>
          )}
        </button>
        {open && files.length > 0 && (
          <div className="border-t border-border">
            {files.map((file) => (
              <ChangedFileRow
                key={file.path}
                file={file}
                onOpenFile={onOpenFile}
              />
            ))}
            {omittedFiles > 0 && (
              <div
                data-testid="turn-changed-files-omitted"
                className="px-3 py-1.5 text-xs text-muted-foreground"
              >
                {omittedFiles} more file{omittedFiles === 1 ? "" : "s"} not
                shown
              </div>
            )}
          </div>
        )}
      </div>
    )
  }
)

function ChangedFileRow({
  file,
  onOpenFile,
}: {
  file: ThreadPrDiffFile
  onOpenFile?: (filePath: string) => void
}) {
  return (
    <button
      type="button"
      data-testid="turn-changed-file"
      onClick={() => onOpenFile?.(file.path)}
      // Dragging a row onto the composer inserts it as an `@file` mention.
      draggable
      onDragStart={(event) => {
        event.dataTransfer.setData(COMPOSER_PATH_DRAG_MIME, file.path)
        event.dataTransfer.effectAllowed = "copy"
      }}
      className="flex w-full items-center justify-between gap-3 border-b border-border px-3 py-1.5 text-left transition-colors last:border-b-0 hover:bg-accent/40"
    >
      <span className="min-w-0 truncate text-[13px] text-foreground/90">
        {file.path}
      </span>
      <span className="flex shrink-0 items-center gap-2 text-xs tabular-nums">
        <span className="text-success-foreground">+{file.additions}</span>
        <span className="text-destructive">-{file.deletions}</span>
      </span>
    </button>
  )
}
