import { PushPinRegularIcon } from "@langchain/macaw-components/icons"
import { ArchiveIcon } from "@phosphor-icons/react/dist/ssr/Archive"
import { ArrowCounterClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowCounterClockwise"
import { CopyIcon } from "@phosphor-icons/react/dist/ssr/Copy"
import { LightningIcon } from "@phosphor-icons/react/dist/ssr/Lightning"
import { Link } from "@tanstack/react-router"
import { PushPinSlashIcon } from "@phosphor-icons/react/dist/ssr/PushPinSlash"
import { SlackLogoIcon } from "@phosphor-icons/react/dist/ssr/SlackLogo"
import { TrashIcon } from "@phosphor-icons/react/dist/ssr/Trash"
import { TreeStructureIcon } from "@phosphor-icons/react/dist/ssr/TreeStructure"

import { ContextMenuItem, DropdownMenuItem } from "@/components/MenuItem"
import type { DesktopLegacyLocalThread } from "@/desktop"
import type { AgentThread } from "@/features/agents/lib/types"

/** Radix scopes items to their menu, so callers name the menu they render in. */
export type ThreadMenuKind = "dropdown" | "context"

const MENU_ITEM = {
  dropdown: DropdownMenuItem,
  context: ContextMenuItem,
} as const

const ICON_SIZE = 14

export function ThreadMenuItems({
  menu = "dropdown",
  thread,
  localThread,
  pinned,
  archived,
  isDeleting,
  onTogglePin,
  onToggleArchived,
  onDelete,
}: {
  menu?: ThreadMenuKind
  thread: AgentThread | null
  localThread?: DesktopLegacyLocalThread
  pinned: boolean
  archived: boolean
  isDeleting: boolean
  onTogglePin: () => void
  onToggleArchived: () => void
  onDelete: () => void
}) {
  const Item = MENU_ITEM[menu]
  const threadId = thread?.id ?? localThread?.id
  return (
    <>
      {thread?.traceUrl && (
        <Item asChild>
          <a href={thread.traceUrl} target="_blank" rel="noreferrer">
            <TreeStructureIcon size={ICON_SIZE} weight="regular" />
            Open trace
          </a>
        </Item>
      )}
      {localThread && (
        <Item
          onSelect={() => {
            void window.openSweDesktop?.openLocalTrace(localThread.id)
          }}
        >
          <TreeStructureIcon size={ICON_SIZE} weight="regular" />
          Open trace
        </Item>
      )}
      {thread?.sourceUrl && (
        <Item asChild>
          <a
            href={thread.sourceAppUrl ?? thread.sourceUrl}
            target="_blank"
            rel="noreferrer"
          >
            <SlackLogoIcon size={ICON_SIZE} weight="regular" />
            Open in Slack
          </a>
        </Item>
      )}
      {thread?.automationId && (
        <Item asChild>
          <Link
            to="/agents/automations/$scheduleId"
            params={{ scheduleId: thread.automationId }}
          >
            <LightningIcon size={ICON_SIZE} weight="regular" />
            Open automation
          </Link>
        </Item>
      )}
      <Item onSelect={onTogglePin}>
        {pinned ? (
          <PushPinSlashIcon size={ICON_SIZE} weight="regular" />
        ) : (
          <PushPinRegularIcon size={ICON_SIZE} />
        )}
        {pinned ? "Unpin thread" : "Pin thread"}
      </Item>
      {thread && (
        <Item
          disabled={!thread.sandboxId}
          onSelect={() => {
            if (thread.sandboxId) {
              void navigator.clipboard.writeText(thread.sandboxId)
            }
          }}
          title={thread.sandboxId ?? undefined}
        >
          <CopyIcon size={ICON_SIZE} weight="regular" />
          Copy sandbox ID
        </Item>
      )}
      {threadId && (
        <Item
          onSelect={() => {
            void navigator.clipboard.writeText(threadId)
          }}
          title={threadId}
        >
          <CopyIcon size={ICON_SIZE} weight="regular" />
          Copy thread ID
        </Item>
      )}
      <Item onSelect={onToggleArchived}>
        {archived ? (
          <ArrowCounterClockwiseIcon size={ICON_SIZE} weight="regular" />
        ) : (
          <ArchiveIcon size={ICON_SIZE} weight="regular" />
        )}
        {archived ? "Unarchive thread" : "Archive thread"}
      </Item>
      <Item
        className="text-error-secondary"
        onSelect={onDelete}
        disabled={isDeleting}
      >
        <TrashIcon size={ICON_SIZE} weight="regular" />
        Delete thread
      </Item>
    </>
  )
}
