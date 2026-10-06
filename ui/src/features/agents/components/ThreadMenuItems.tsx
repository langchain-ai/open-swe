import {
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "@langchain/gtm-platform-design-system/ui/dropdown-menu"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"
import { ProviderLogo } from "@langchain/gtm-platform-design-system/ui/provider-logos"

import type { DesktopLegacyLocalThread } from "@/desktop"
import type { AgentThread } from "@/features/agents/lib/types"
import {
  ArchiveBox,
  Copy,
  PinOff,
  PushPin,
  RotateCcw,
  Trash2,
  TreeStructure,
} from "@/components/glyphs"
import { reportError } from "@/lib/errorReporting"

function copyToClipboard(value: string, title: string) {
  void navigator.clipboard
    .writeText(value)
    .catch((error: unknown) => reportError({ title, error }))
}

/**
 * The thread's actions, shared by the rail row's menu, its context menu and
 * the thread band. Grouped as the rail law asks: where the thread came from,
 * how it is organised, then the verbs that take it off the list.
 */
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
  localThread?: DesktopLegacyLocalThread
  pinned: boolean
  archived: boolean
  isDeleting: boolean
  onTogglePin: () => void
  onToggleArchived: () => void
  onDelete: () => void
}) {
  const threadId = thread?.id ?? localThread?.id
  const hasSourceLinks = Boolean(
    thread?.traceUrl || localThread || thread?.sourceUrl
  )
  return (
    <>
      {hasSourceLinks && (
        <>
          <DropdownMenuGroup>
            {thread?.traceUrl && (
              <DropdownMenuItem
                render={
                  <a href={thread.traceUrl} target="_blank" rel="noreferrer" />
                }
              >
                <Icon icon={TreeStructure} size="sm" />
                Open trace
              </DropdownMenuItem>
            )}
            {localThread && (
              <DropdownMenuItem
                onClick={() => {
                  void window.openSweDesktop
                    ?.openLocalTrace(localThread.id)
                    .catch((error: unknown) =>
                      reportError({ title: "Couldn't open trace", error })
                    )
                }}
              >
                <Icon icon={TreeStructure} size="sm" />
                Open trace
              </DropdownMenuItem>
            )}
            {thread?.sourceUrl && (
              <DropdownMenuItem
                render={
                  <a
                    href={thread.sourceAppUrl ?? thread.sourceUrl}
                    target="_blank"
                    rel="noreferrer"
                  />
                }
              >
                <ProviderLogo provider="slack" className="size-3.5" />
                Open in Slack
              </DropdownMenuItem>
            )}
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
        </>
      )}
      <DropdownMenuGroup>
        <DropdownMenuItem onClick={onTogglePin}>
          <Icon icon={pinned ? PinOff : PushPin} size="sm" />
          {pinned ? "Unpin thread" : "Pin thread"}
        </DropdownMenuItem>
        {thread && (
          <DropdownMenuItem
            disabled={!thread.sandboxId}
            title={thread.sandboxId ?? undefined}
            onClick={() => {
              if (thread.sandboxId)
                copyToClipboard(thread.sandboxId, "Couldn't copy sandbox ID")
            }}
          >
            <Icon icon={Copy} size="sm" />
            Copy sandbox ID
          </DropdownMenuItem>
        )}
        {threadId && (
          <DropdownMenuItem
            title={threadId}
            onClick={() => copyToClipboard(threadId, "Couldn't copy thread ID")}
          >
            <Icon icon={Copy} size="sm" />
            Copy thread ID
          </DropdownMenuItem>
        )}
      </DropdownMenuGroup>
      <DropdownMenuSeparator />
      <DropdownMenuGroup>
        <DropdownMenuItem onClick={onToggleArchived}>
          <Icon icon={archived ? RotateCcw : ArchiveBox} size="sm" />
          {archived ? "Unarchive thread" : "Archive thread"}
        </DropdownMenuItem>
        <DropdownMenuItem
          variant="destructive"
          disabled={isDeleting}
          onClick={onDelete}
        >
          <Icon icon={Trash2} size="sm" />
          Delete thread
        </DropdownMenuItem>
      </DropdownMenuGroup>
    </>
  )
}
