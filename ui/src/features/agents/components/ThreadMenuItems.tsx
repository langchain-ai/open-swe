import { SlackLinks } from "@/lib/slack-links"
import { PushPinRegularIcon } from "@langchain/macaw-components/icons"
import { ContextMenuSeparator } from "@langchain/macaw-components/ContextMenu"
import { DropdownMenuSeparator } from "@langchain/macaw-components/DropdownMenu"
import { ArchiveIcon } from "@phosphor-icons/react/dist/ssr/Archive"
import { ArrowCounterClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowCounterClockwise"
import { ArrowLeftIcon } from "@phosphor-icons/react/dist/ssr/ArrowLeft"
import { CaretRightIcon } from "@phosphor-icons/react/dist/ssr/CaretRight"
import { CodeIcon } from "@phosphor-icons/react/dist/ssr/Code"
import { CopyIcon } from "@phosphor-icons/react/dist/ssr/Copy"
import { EyeIcon } from "@phosphor-icons/react/dist/ssr/Eye"
import { EyeSlashIcon } from "@phosphor-icons/react/dist/ssr/EyeSlash"
import { PencilSimpleIcon } from "@phosphor-icons/react/dist/ssr/PencilSimple"
import { LightningIcon } from "@phosphor-icons/react/dist/ssr/Lightning"
import { Link } from "@tanstack/react-router"
import { PushPinSlashIcon } from "@phosphor-icons/react/dist/ssr/PushPinSlash"
import { SlackLogoIcon } from "@phosphor-icons/react/dist/ssr/SlackLogo"
import { TrashIcon } from "@phosphor-icons/react/dist/ssr/Trash"
import { TreeStructureIcon } from "@phosphor-icons/react/dist/ssr/TreeStructure"
import { useState } from "react"

import { ContextMenuItem, DropdownMenuItem } from "@/components/MenuItem"
import type { AgentThread } from "@/features/agents/lib/types"

export type ThreadMenuKind = "dropdown" | "context"

const MENU = {
  dropdown: { Item: DropdownMenuItem, Separator: DropdownMenuSeparator },
  context: { Item: ContextMenuItem, Separator: ContextMenuSeparator },
} as const

const ICON_SIZE = 14

export function ThreadMenuItems({
  menu = "dropdown",
  thread,
  pinned,
  archived,
  isDeleting,
  onRename,
  onTogglePin,
  onToggleViewed,
  onToggleArchived,
  onDelete,
}: {
  menu?: ThreadMenuKind
  thread: AgentThread
  pinned: boolean
  archived: boolean
  isDeleting: boolean
  onRename?: () => void
  onTogglePin: () => void
  onToggleViewed: () => void
  onToggleArchived: () => void
  onDelete: () => void
}) {
  const { Item, Separator } = MENU[menu]
  const [diagnostics, setDiagnostics] = useState(false)
  if (diagnostics) {
    return (
      <>
        <Item
          onKeyDown={(event) => {
            if (event.key === "ArrowLeft") {
              event.preventDefault()
              setDiagnostics(false)
            }
          }}
          onSelect={(event) => {
            event.preventDefault()
            setDiagnostics(false)
          }}
        >
          <ArrowLeftIcon size={ICON_SIZE} />
          Back
        </Item>
        <Separator />
        {thread.traceUrl && (
          <Item asChild>
            <a href={thread.traceUrl} target="_blank" rel="noreferrer">
              <TreeStructureIcon size={ICON_SIZE} />
              Open trace
            </a>
          </Item>
        )}
        <Item
          disabled={!thread.sandboxId}
          onSelect={() => {
            if (thread.sandboxId)
              void navigator.clipboard.writeText(thread.sandboxId)
          }}
        >
          <CopyIcon size={ICON_SIZE} />
          Copy sandbox ID
        </Item>
        <Item
          onSelect={() => {
            void navigator.clipboard.writeText(thread.id)
          }}
        >
          <CopyIcon size={ICON_SIZE} />
          Copy thread ID
        </Item>
      </>
    )
  }
  return (
    <>
      {onRename && (
        <Item onSelect={onRename}>
          <PencilSimpleIcon size={ICON_SIZE} />
          Rename
        </Item>
      )}
      <Item onSelect={onTogglePin}>
        {pinned ? (
          <PushPinSlashIcon size={ICON_SIZE} />
        ) : (
          <PushPinRegularIcon size={ICON_SIZE} />
        )}
        {pinned ? "Unpin" : "Pin"}
      </Item>
      <Item onSelect={onToggleViewed}>
        {thread.viewed ? (
          <EyeSlashIcon size={ICON_SIZE} />
        ) : (
          <EyeIcon size={ICON_SIZE} />
        )}
        {thread.viewed ? "Mark unread" : "Mark read"}
      </Item>
      {(thread.sourceUrl || thread.automationId) && <Separator />}
      {thread.sourceUrl && (
        <Item asChild>
          <a
            href={SlackLinks.normalize(thread.sourceAppUrl ?? thread.sourceUrl)}
            target="_blank"
            rel="noreferrer"
          >
            <SlackLogoIcon size={ICON_SIZE} />
            Open in Slack
          </a>
        </Item>
      )}
      {thread.automationId && (
        <Item asChild>
          <Link
            to="/agents/automations/$scheduleId"
            params={{ scheduleId: thread.automationId }}
          >
            <LightningIcon size={ICON_SIZE} />
            Open automation
          </Link>
        </Item>
      )}
      <Separator />
      <Item
        onKeyDown={(event) => {
          if (event.key === "ArrowRight") {
            event.preventDefault()
            setDiagnostics(true)
          }
        }}
        onSelect={(event) => {
          event.preventDefault()
          setDiagnostics(true)
        }}
      >
        <CodeIcon size={ICON_SIZE} />
        Diagnostics
        <CaretRightIcon size={ICON_SIZE} className="ml-auto" />
      </Item>
      <Separator />
      <Item onSelect={onToggleArchived}>
        {archived ? (
          <ArrowCounterClockwiseIcon size={ICON_SIZE} />
        ) : (
          <ArchiveIcon size={ICON_SIZE} />
        )}
        {archived ? "Unarchive" : "Archive"}
      </Item>
      <Item
        className="text-error-secondary"
        onSelect={onDelete}
        disabled={isDeleting}
      >
        <TrashIcon size={ICON_SIZE} />
        {isDeleting ? "Deleting…" : "Delete"}
      </Item>
    </>
  )
}
