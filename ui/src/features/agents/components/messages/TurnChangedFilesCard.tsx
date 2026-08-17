import { memo, useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"

import type { ThreadPrDiffFile } from "@/features/agents/lib/api"
import { COMPOSER_PATH_DRAG_MIME } from "@/features/agents/components/composer/composerTrigger"
import { useAgentThreadTurnDiff } from "@/features/agents/lib/queries"

/**
 * What a turn changed, according to git — not to the edit calls in the
 * transcript, which miss edits made through `execute` and still list files that
 * were later reverted. Older turns only fetch once opened; the newest turn is
 * the one people look at, so it loads with the transcript.
 */
export const TurnChangedFilesCard = memo(function TurnChangedFilesCard({
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
  const turnDiff = useAgentThreadTurnDiff(threadId, turnKey, open)
  const files = turnDiff.data?.files ?? []

  if (
    turnDiff.data?.status === "missing" ||
    (turnDiff.isFetched && files.length === 0)
  ) {
    return null
  }

  const additions = files.reduce((total, file) => total + file.additions, 0)
  const deletions = files.reduce((total, file) => total + file.deletions, 0)

  return (
    <div className="mt-3 overflow-hidden rounded-xl bg-muted/40">
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
        ) : files.length > 0 ? (
          <>
            <span>
              {files.length} file{files.length === 1 ? "" : "s"} changed
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
        </div>
      )}
    </div>
  )
})

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
