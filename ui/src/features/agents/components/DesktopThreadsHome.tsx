import { Link } from "@tanstack/react-router"
import { CircleNotchIcon } from "@phosphor-icons/react"
import { IoCloudOutline, IoDesktopOutline } from "react-icons/io5"

import { useDesktopAgentTabs } from "./DesktopAgentTabs"
import {
  useDesktopLocalThreads,
  useLocalThreadActivity,
} from "@/features/agents/lib/desktopLocal"
import { useSidebarThreads } from "@/features/agents/lib/queries"
import { cn, formatRelativeTime } from "@/lib/utils"

export function DesktopThreadsHome({
  cloudEnabled,
  onNewThread,
}: {
  cloudEnabled: boolean
  onNewThread: () => void
}) {
  const tabs = useDesktopAgentTabs()
  const localThreads = useDesktopLocalThreads().data ?? []
  const localActivity = useLocalThreadActivity()
  const cloud = useSidebarThreads({
    includeAutomations: true,
    includeResolved: true,
    enabled: cloudEnabled,
  })
  const threads = [
    ...[...cloud.data.active.items, ...cloud.data.resolved.items].map(
      (thread) => ({
        id: thread.id,
        source: "cloud" as const,
        title: thread.title,
        subtitle: thread.repoFullName || thread.repo || "Cloud",
        updatedAt: thread.updatedAt,
        running: thread.status === "running",
      })
    ),
    ...localThreads.map((thread) => ({
      id: thread.id,
      source: "local" as const,
      title: thread.title,
      subtitle: thread.cwd,
      updatedAt: thread.updatedAt,
      running: localActivity[thread.id] === "running",
    })),
  ].sort((left, right) => right.updatedAt - left.updatedAt)

  return (
    <section className="mx-auto flex w-full max-w-4xl flex-col overflow-y-auto px-6 py-10">
      <div className="mb-6 flex items-end justify-between gap-4">
        <div>
          <h1 className="font-heading text-2xl font-medium tracking-tight">
            Home
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Cloud and local threads
          </p>
        </div>
        <button
          type="button"
          onClick={onNewThread}
          className="rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground"
        >
          New thread
        </button>
      </div>
      <div className="overflow-hidden rounded-xl border border-border bg-card">
        {threads.map((thread) => {
          const Icon =
            thread.source === "cloud" ? IoCloudOutline : IoDesktopOutline
          return (
            <Link
              key={`${thread.source}:${thread.id}`}
              to={
                thread.source === "local"
                  ? "/agents/local/$sessionId"
                  : "/agents/$threadId"
              }
              params={
                thread.source === "local"
                  ? { sessionId: thread.id }
                  : { threadId: thread.id }
              }
              onClick={() => tabs?.open(thread)}
              className="flex items-center gap-3 border-b border-border px-4 py-3 last:border-b-0 hover:bg-muted/40"
            >
              <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                <Icon className="size-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">
                  {thread.title}
                </div>
                <div className="truncate text-xs text-muted-foreground">
                  {thread.subtitle}
                </div>
              </div>
              {thread.running && (
                <CircleNotchIcon className="size-4 animate-spin text-primary" />
              )}
              <span
                className={cn(
                  "shrink-0 text-xs text-muted-foreground",
                  thread.running && "hidden sm:block"
                )}
              >
                {formatRelativeTime(thread.updatedAt)}
              </span>
            </Link>
          )
        })}
        {!cloud.isPending && threads.length === 0 && (
          <p className="px-4 py-12 text-center text-sm text-muted-foreground">
            No threads yet
          </p>
        )}
      </div>
    </section>
  )
}
