import type { ReactNode } from "react"
import { Link } from "@tanstack/react-router"
import {
  ThreadListItemPrimitive,
  ThreadListPrimitive,
  useAui,
  useAuiState,
} from "@assistant-ui/react"
import { Plus } from "lucide-react"
import { TooltipProvider } from "@/components/ui/tooltip"

export function AssistantShell({ children }: { children: ReactNode }) {
  const aui = useAui()
  const hasMore = useAuiState((state) => state.threads.hasMore)
  return (
    <TooltipProvider>
      <div className="agents-ui flex h-svh min-h-0 bg-background text-foreground">
        <aside
          data-sidebar-frame
          className="flex w-60 shrink-0 flex-col border-r border-border bg-sidebar p-3 max-sm:w-40"
        >
          <Link
            to="/assistant"
            className="mb-4 px-2 py-1 text-sm font-semibold"
          >
            Open SWE
          </Link>
          <ThreadListPrimitive.Root className="flex min-h-0 flex-1 flex-col">
            <ThreadListPrimitive.New className="mb-3 flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm">
              <Plus className="size-4" />
              New conversation
            </ThreadListPrimitive.New>
            <div className="min-h-0 flex-1 overflow-y-auto">
              <ThreadListPrimitive.Items>
                {({ threadListItem }) => (
                  <ThreadListItemPrimitive.Root className="mb-1 rounded-lg hover:bg-accent data-[active=true]:bg-accent">
                    <ThreadListItemPrimitive.Trigger asChild>
                      <Link
                        to="/assistant/$threadId"
                        params={{
                          threadId:
                            threadListItem.remoteId ?? threadListItem.id,
                        }}
                        className="flex items-center gap-2 px-3 py-2 text-sm"
                      >
                        {threadListItem.isRunning && (
                          <span
                            aria-label="Running"
                            className="size-1.5 shrink-0 animate-pulse rounded-full bg-primary"
                          />
                        )}
                        <span className="truncate">
                          <ThreadListItemPrimitive.Title fallback="New conversation" />
                        </span>
                      </Link>
                    </ThreadListItemPrimitive.Trigger>
                  </ThreadListItemPrimitive.Root>
                )}
              </ThreadListPrimitive.Items>
              {hasMore && (
                <button
                  onClick={() => void aui.threads().loadMore()}
                  className="px-3 py-2 text-xs underline"
                >
                  Load more
                </button>
              )}
            </div>
          </ThreadListPrimitive.Root>
          <nav className="mt-3 flex flex-col gap-2 border-t border-border px-2 pt-3 text-xs text-muted-foreground">
            <Link
              to="/agents/threads"
              search={{ page: 1, layout: "board", group: "focus" }}
            >
              All tasks
            </Link>
            <Link to="/agents/automations">Automations</Link>
            <Link to="/agents/skills">Skills</Link>
            <Link to="/my-settings">Settings</Link>
          </nav>
        </aside>
        <main className="flex min-h-0 min-w-0 flex-1">{children}</main>
      </div>
    </TooltipProvider>
  )
}
