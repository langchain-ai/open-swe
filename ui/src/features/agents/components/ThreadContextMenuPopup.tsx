import { ContextMenu } from "@base-ui/react/context-menu"
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

import type { AgentThread } from "@/features/agents/lib/types"

export function ThreadContextMenuPopup({
  thread,
  pinned,
  archived,
  isDeleting,
  onTogglePin,
  onToggleArchived,
  onDelete,
}: {
  thread: AgentThread | null
  pinned: boolean
  archived: boolean
  isDeleting: boolean
  onTogglePin: () => void
  onToggleArchived: () => void
  onDelete: () => void
}) {
  return (
    <ContextMenu.Portal>
      <ContextMenu.Positioner className="z-50 outline-none">
        <ContextMenu.Popup className="min-w-[10rem] overflow-hidden rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
          {thread?.traceUrl && (
            <ContextMenu.LinkItem
              href={thread.traceUrl}
              target="_blank"
              rel="noreferrer"
              closeOnClick
              className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs outline-none select-none data-highlighted:bg-muted"
            >
              <TreeStructureIcon className="size-3.5" />
              Open trace
            </ContextMenu.LinkItem>
          )}
          {thread?.sourceUrl && (
            <ContextMenu.LinkItem
              href={thread.sourceAppUrl ?? thread.sourceUrl}
              closeOnClick
              className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs outline-none select-none data-highlighted:bg-muted"
            >
              <IoLogoSlack className="size-3.5" />
              Open in Slack
            </ContextMenu.LinkItem>
          )}
          <ContextMenu.Item
            onClick={onTogglePin}
            className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs outline-none select-none data-highlighted:bg-muted"
          >
            {pinned ? (
              <PushPinSlashIcon className="size-3.5" />
            ) : (
              <PushPinIcon className="size-3.5" />
            )}
            {pinned ? "Unpin thread" : "Pin thread"}
          </ContextMenu.Item>
          {thread && (
            <ContextMenu.Item
              disabled={!thread.sandboxId}
              onClick={() => {
                if (thread.sandboxId) {
                  void navigator.clipboard.writeText(thread.sandboxId)
                }
              }}
              title={thread.sandboxId ?? undefined}
              className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs outline-none select-none data-highlighted:bg-muted data-disabled:pointer-events-none data-disabled:opacity-50"
            >
              <CopyIcon className="size-3.5" />
              Copy sandbox ID
            </ContextMenu.Item>
          )}
          <ContextMenu.Item
            onClick={onToggleArchived}
            className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs outline-none select-none data-highlighted:bg-muted"
          >
            {archived ? (
              <ArrowCounterClockwiseIcon className="size-3.5" />
            ) : (
              <ArchiveIcon className="size-3.5" />
            )}
            {archived ? "Unarchive thread" : "Archive thread"}
          </ContextMenu.Item>
          <ContextMenu.Item
            onClick={onDelete}
            disabled={isDeleting}
            className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-xs text-destructive outline-none select-none data-highlighted:bg-muted data-disabled:pointer-events-none data-disabled:opacity-50"
          >
            <TrashIcon className="size-3.5" />
            Delete thread
          </ContextMenu.Item>
        </ContextMenu.Popup>
      </ContextMenu.Positioner>
    </ContextMenu.Portal>
  )
}
