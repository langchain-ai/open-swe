import {
  ArchiveIcon,
  ArrowCounterClockwiseIcon,
  CopyIcon,
  PushPinIcon,
  PushPinSlashIcon,
  TrashIcon,
  TreeStructureIcon,
} from "@phosphor-icons/react"
import { IoLogoSlack } from "react-icons/io5"

import { MenuItem } from "@/components/ui/menu"
import type { DesktopLocalThreadSummary } from "@/desktop"
import type { AgentThread } from "@/features/agents/lib/types"
import { useCopyToClipboard } from "@/lib/useCopyToClipboard"

export function ThreadMenuItems({
  thread,
  localThread,
  pinned,
  archived,
  isDeleting,
  onTogglePin,
  onToggleArchived,
  onDelete,
}: {
  thread: AgentThread | null
  localThread?: DesktopLocalThreadSummary
  pinned: boolean
  archived: boolean
  isDeleting: boolean
  onTogglePin: () => void
  onToggleArchived: () => void
  onDelete: () => void
}) {
  const { copy } = useCopyToClipboard()
  const threadId = thread?.id ?? localThread?.id
  return (
    <>
      {thread?.traceUrl && (
        <MenuItem
          render={<a href={thread.traceUrl} target="_blank" rel="noreferrer" />}
        >
          <TreeStructureIcon />
          Open trace
        </MenuItem>
      )}
      {localThread && (
        <MenuItem
          onClick={() => {
            void window.openSweDesktop?.openLocalTrace(localThread.id)
          }}
        >
          <TreeStructureIcon />
          Open trace
        </MenuItem>
      )}
      {thread?.sourceUrl && (
        <MenuItem
          render={
            <a
              href={thread.sourceAppUrl ?? thread.sourceUrl}
              target="_blank"
              rel="noreferrer"
            />
          }
        >
          <IoLogoSlack />
          Open in Slack
        </MenuItem>
      )}
      <MenuItem onClick={onTogglePin}>
        {pinned ? <PushPinSlashIcon /> : <PushPinIcon />}
        {pinned ? "Unpin thread" : "Pin thread"}
      </MenuItem>
      {thread && (
        <MenuItem
          disabled={!thread.sandboxId}
          onClick={() => {
            if (thread.sandboxId) {
              void copy(thread.sandboxId)
            }
          }}
        >
          <CopyIcon />
          Copy sandbox ID
        </MenuItem>
      )}
      {threadId && (
        <MenuItem
          onClick={() => {
            void copy(threadId)
          }}
        >
          <CopyIcon />
          Copy thread ID
        </MenuItem>
      )}
      <MenuItem onClick={onToggleArchived}>
        {archived ? <ArrowCounterClockwiseIcon /> : <ArchiveIcon />}
        {archived ? "Unarchive thread" : "Archive thread"}
      </MenuItem>
      <MenuItem variant="destructive" onClick={onDelete} disabled={isDeleting}>
        <TrashIcon />
        Delete thread
      </MenuItem>
    </>
  )
}
