import { useState } from "react"
import { useNavigate } from "@tanstack/react-router"
import { Button } from "@langchain/macaw-components/Button"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { SidebarThreadRow } from "@/features/agents/components/SidebarThreadRow"
import {
  cloudSidebarThread,
  withoutNestedWorkers,
} from "@/features/agents/lib/sidebarThreads"
import {
  usePinAgentThread,
  useResolveAgentThread,
  useSidebarPinnedThreads,
} from "@/features/agents/lib/queries"
import { useProjectThreads } from "@/features/agents/lib/chatProjects"
import { useRunCompletionNotifier } from "@/features/agents/lib/useRunCompletionNotifier"

export function ProjectThreads({
  projectId,
  activeThreadId,
  onNavigate,
  preview = false,
}: {
  projectId: string
  activeThreadId?: string
  onNavigate?: () => void
  preview?: boolean
}) {
  const query = useProjectThreads(projectId)
  const navigate = useNavigate()
  const pinned = useSidebarPinnedThreads()
  const pin = usePinAgentThread()
  const archive = useResolveAgentThread()
  const [limit, setLimit] = useState(preview ? 5 : 50)
  const threads = withoutNestedWorkers(
    query.data?.pages.flatMap((page) => page.items) ?? []
  ).filter((thread) => !thread.resolved)
  useRunCompletionNotifier(threads, activeThreadId, (threadId) => {
    void navigate({ to: "/agents/$threadId", params: { threadId } })
  })
  const busy = pin.isPending || archive.isPending
  return (
    <div>
      {query.isPending && (
        <div
          role="status"
          className="flex items-center gap-space-2 px-space-2 py-space-2 text-xs text-secondary"
        >
          <Spinner size="xs" />
          Loading chats…
        </div>
      )}
      {query.isError && (
        <div role="alert" className="text-error px-space-2 text-xs">
          Could not load project chats.{" "}
          <Button
            size="xs"
            color="secondary"
            variant="plain"
            onClick={() => void query.refetch()}
          >
            Retry
          </Button>
        </div>
      )}
      {!query.isPending && !query.isError && threads.length === 0 && (
        <p className="px-space-2 py-space-2 text-xs text-tertiary">
          No chats yet.
        </p>
      )}
      {threads.slice(0, limit).map((thread) => {
        const isPinned =
          pinned.data?.some((item) => item.id === thread.id) ?? false
        return (
          <SidebarThreadRow
            key={thread.id}
            item={cloudSidebarThread(thread)}
            isActive={thread.id === activeThreadId}
            pinned={isPinned}
            archived={false}
            pending={busy}
            onNavigate={onNavigate}
            onTogglePin={() =>
              pin.mutate({ threadId: thread.id, pinned: !isPinned })
            }
            onToggleArchived={() =>
              archive.mutate({ threadId: thread.id, resolved: true })
            }
          />
        )
      })}
      {(threads.length > limit || query.hasNextPage) && (
        <Button
          size="xs"
          color="secondary"
          variant="plain"
          className="ml-space-2 text-secondary"
          disabled={query.isFetchingNextPage}
          onClick={() => {
            setLimit((current) => current + (preview ? 5 : 50))
            if (query.hasNextPage) void query.fetchNextPage()
          }}
        >
          {query.isFetchingNextPage ? "Loading…" : "Show more"}
        </Button>
      )}
    </div>
  )
}
