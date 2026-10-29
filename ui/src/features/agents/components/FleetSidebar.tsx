import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { Button } from "@langchain/macaw-components/Button"
import { Select } from "@langchain/macaw-components/Select"
import {
  fleetRequest,
  type Thread,
  type Workspace,
} from "@/features/agents/lib/fleetChat"
import { cn } from "@/lib/utils"

export function FleetSidebar({
  workspaceId,
  threadId,
  onSelect,
}: {
  workspaceId?: string
  threadId?: string
  onSelect: (workspace?: string, thread?: string) => void
}) {
  const workspaces = useQuery({
    queryKey: ["fleet-workspaces"],
    queryFn: () => fleetRequest<Workspace[]>("/workspaces"),
    retry: false,
  })
  const threads = useQuery({
    queryKey: ["fleet-threads", workspaceId],
    queryFn: () => fleetRequest<Thread[]>(`/${workspaceId}/threads`),
    enabled: Boolean(workspaceId),
    retry: false,
  })
  const problem = workspaces.error?.message ?? threads.error?.message
  return (
    <div
      className="flex min-h-0 flex-1 flex-col gap-space-3 overflow-y-auto px-space-2 pb-space-3"
      aria-label="Fleet sidebar"
    >
      <Button
        size="md"
        color="secondary"
        className="w-full justify-start"
        disabled={!workspaceId}
        onClick={() => onSelect(workspaceId)}
      >
        New chat
      </Button>
      <Select
        aria-label="LangSmith workspace"
        size="md"
        placeholder="Select LangSmith workspace"
        value={workspaceId}
        options={(workspaces.data ?? []).map((workspace) => ({
          value: workspace.id,
          label: workspace.name,
        }))}
        loading={workspaces.isPending}
        onChange={(workspace) => onSelect(workspace)}
        triggerClassName="w-full"
      />
      {problem && (
        <div
          role="alert"
          className="flex flex-col gap-space-2 px-space-2 text-sm text-secondary"
        >
          <p>{problem}</p>
          <Link to="/my-settings/connections" className="underline">
            Connections
          </Link>
          <Button
            size="sm"
            color="secondary"
            onClick={() => {
              void workspaces.refetch()
              if (workspaceId) void threads.refetch()
            }}
          >
            Retry
          </Button>
        </div>
      )}
      <h2 className="px-space-2 text-xs font-medium text-secondary">
        Conversations
      </h2>
      {threads.isLoading && (
        <p role="status" className="px-space-2 text-sm text-secondary">
          Loading chats…
        </p>
      )}
      {workspaceId && threads.data?.length === 0 && (
        <p className="px-space-2 text-sm text-secondary">
          No Fleet conversations yet.
        </p>
      )}
      {(threads.data ?? []).map((thread) => (
        <Link
          key={thread.thread_id}
          to="/agents/chat"
          search={{ workspace: workspaceId, chat: thread.thread_id }}
          onClick={() => onSelect(workspaceId, thread.thread_id)}
          aria-current={thread.thread_id === threadId ? "page" : undefined}
          className={cn(
            "rounded-md px-space-2 py-space-2 text-sm text-primary hover:bg-surface-level-2-hover",
            thread.thread_id === threadId && "bg-selected"
          )}
        >
          <span className="block truncate">
            {thread.metadata?.thread_title ??
              thread.metadata?.first_message_preview ??
              "New chat"}
          </span>
        </Link>
      ))}
    </div>
  )
}
