import { Menu } from "@base-ui/react/menu"
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

import type { DesktopLocalThreadSummary } from "@/desktop"
import type { AgentThread } from "@/features/agents/lib/types"

const menuItemClassName =
  "flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs outline-none select-none data-highlighted:bg-muted"

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
  const threadId = thread?.id ?? localThread?.id
  return (
    <>
      {thread?.traceUrl && (
        <Menu.LinkItem
          href={thread.traceUrl}
          target="_blank"
          rel="noreferrer"
          closeOnClick
          className={menuItemClassName}
        >
          <TreeStructureIcon className="size-3.5" />
          Open trace
        </Menu.LinkItem>
      )}
      {localThread && (
        <Menu.Item
          onClick={() => {
            void window.openSweDesktop?.openLocalTrace(localThread.id)
          }}
          className={menuItemClassName}
        >
          <TreeStructureIcon className="size-3.5" />
          Open trace
        </Menu.Item>
      )}
      {thread?.sourceUrl && (
        <Menu.LinkItem
          href={thread.sourceAppUrl ?? thread.sourceUrl}
          closeOnClick
          className={menuItemClassName}
        >
          <IoLogoSlack className="size-3.5" />
          Open in Slack
        </Menu.LinkItem>
      )}
      <Menu.Item onClick={onTogglePin} className={menuItemClassName}>
        {pinned ? (
          <PushPinSlashIcon className="size-3.5" />
        ) : (
          <PushPinIcon className="size-3.5" />
        )}
        {pinned ? "Unpin thread" : "Pin thread"}
      </Menu.Item>
      {thread && (
        <Menu.Item
          disabled={!thread.sandboxId}
          onClick={() => {
            if (thread.sandboxId) {
              void navigator.clipboard.writeText(thread.sandboxId)
            }
          }}
          title={thread.sandboxId ?? undefined}
          className={`${menuItemClassName} data-disabled:pointer-events-none data-disabled:opacity-50`}
        >
          <CopyIcon className="size-3.5" />
          Copy sandbox ID
        </Menu.Item>
      )}
      {threadId && (
        <Menu.Item
          onClick={() => {
            void navigator.clipboard.writeText(threadId)
          }}
          title={threadId}
          className={menuItemClassName}
        >
          <CopyIcon className="size-3.5" />
          Copy thread ID
        </Menu.Item>
      )}
      <Menu.Item onClick={onToggleArchived} className={menuItemClassName}>
        {archived ? (
          <ArrowCounterClockwiseIcon className="size-3.5" />
        ) : (
          <ArchiveIcon className="size-3.5" />
        )}
        {archived ? "Unarchive thread" : "Archive thread"}
      </Menu.Item>
      <Menu.Item
        onClick={onDelete}
        disabled={isDeleting}
        className={`${menuItemClassName} text-destructive data-disabled:pointer-events-none data-disabled:opacity-50`}
      >
        <TrashIcon className="size-3.5" />
        Delete thread
      </Menu.Item>
    </>
  )
}
