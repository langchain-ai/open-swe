import { ContextMenu } from "@base-ui/react/context-menu"
import { FolderOpen } from "lucide-react"
import { useState } from "react"

import { useSidebarCollapsed } from "@/components/sidebar-layout"
import { DeleteThreadDialog } from "@/features/agents/components/DeleteThreadDialog"
import { ThreadContextMenuPopup } from "@/features/agents/components/ThreadContextMenuPopup"
import type { AgentThread } from "@/features/agents/lib/types"
import {
  useDeleteAgentThread,
  usePinAgentThread,
  useResolveAgentThread,
  useSidebarPinnedThreads,
} from "@/features/agents/lib/queries"
import { cn } from "@/lib/utils"

export function AgentThreadHeader({
  title,
  project,
  target,
  panelCollapsed,
  thread,
}: {
  title?: string | null
  project?: string | null
  target: "Cloud" | "This Mac"
  panelCollapsed: boolean
  thread?: AgentThread
}) {
  const sidebarCollapsed = useSidebarCollapsed()
  const isDesktop =
    typeof window !== "undefined" && Boolean(window.openSweDesktop)
  const pinnedThreads = useSidebarPinnedThreads({ enabled: Boolean(thread) })
  const pinThread = usePinAgentThread()
  const resolveThread = useResolveAgentThread()
  const deleteThread = useDeleteAgentThread()
  const [deleteOpen, setDeleteOpen] = useState(false)
  const pinned =
    thread !== undefined &&
    Boolean(pinnedThreads.data?.some((candidate) => candidate.id === thread.id))
  const archived = thread?.resolved === true

  const header = (
    <header
      data-desktop-drag-region=""
      className="relative z-10 h-11 shrink-0 border-b border-border/60 bg-background/80 after:pointer-events-none after:absolute after:inset-x-0 after:top-full after:h-4 after:bg-linear-to-b after:from-background/60 after:to-transparent"
    >
      <div
        className={cn(
          "flex h-full w-full items-center gap-3 px-4",
          sidebarCollapsed && (isDesktop ? "pl-32" : "pl-14"),
          panelCollapsed && "pr-14"
        )}
      >
        {title && (
          <span
            className="min-w-0 flex-1 truncate text-sm font-medium"
            title={title}
          >
            {title}
          </span>
        )}
        {project && (
          <span className="flex max-w-[40%] min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
            <FolderOpen className="size-3.5 shrink-0" />
            <span className="truncate" title={project}>
              {project}
            </span>
          </span>
        )}
        <span className="ml-auto shrink-0 text-xs text-muted-foreground">
          {target}
        </span>
      </div>
    </header>
  )

  if (!thread) return header

  return (
    <>
      <ContextMenu.Root>
        <ContextMenu.Trigger render={header} />
        <ThreadContextMenuPopup
          thread={thread}
          pinned={pinned}
          archived={archived}
          isDeleting={deleteThread.isPending}
          onTogglePin={() => {
            if (!pinThread.isPending) {
              pinThread.mutate({ threadId: thread.id, pinned: !pinned })
            }
          }}
          onToggleArchived={() => {
            if (!resolveThread.isPending) {
              resolveThread.mutate({
                threadId: thread.id,
                resolved: !archived,
              })
            }
          }}
          onDelete={() => setDeleteOpen(true)}
        />
      </ContextMenu.Root>
      <DeleteThreadDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        threadTitle={thread.title}
        isDeleting={deleteThread.isPending}
        onConfirm={() =>
          deleteThread.mutate(thread.id, {
            onSuccess: () => setDeleteOpen(false),
          })
        }
      />
    </>
  )
}
