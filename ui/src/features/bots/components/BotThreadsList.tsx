import { Link } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import {
  ArrowSquareOutIcon,
  CircleNotchIcon,
  RobotIcon,
} from "@phosphor-icons/react"
import { IoLogoSlack } from "react-icons/io5"

import type { AgentStatus, AgentThread } from "@/features/agents/lib/types"
import type { AllowedSlackBotEntry } from "@/lib/api"
import { Avatar, AvatarFallback, AvatarImage } from "@langchain/gtm-platform-design-system/ui/avatar"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { useThreadsPage } from "@/features/agents/lib/queries"
import { AllowedSlackBotsSection } from "@/features/bots/components/AllowedSlackBotsSection"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"
import { cn, formatRelativeTime } from "@/lib/utils"

const THREAD_LIMIT = 50

const STATUS_LABELS: Record<AgentStatus, string> = {
  running: "Running",
  finished: "Finished",
  interrupted: "Interrupted",
  error: "Error",
  idle: "Idle",
}

export function BotThreadsList({
  bot,
  onBotChange,
}: {
  bot?: string
  onBotChange: (bot: string | undefined) => void
}) {
  const directory = useQuery({
    queryKey: ["allowedSlackBotDirectory"],
    queryFn: api.listAllowedSlackBotDirectory,
    staleTime: 5 * 60 * 1000,
  })
  const bots = directory.data ?? []
  const threadsQuery = useThreadsPage(
    { limit: THREAD_LIMIT, offset: 0, scope: "bot", bot },
    { enabled: !!bot, pollWhileRunning: !!bot }
  )
  const threads = threadsQuery.data?.items ?? []
  const botsByKey = new Map(bots.map((entry) => [entry.key, entry]))
  const isAdmin = useSession().data?.is_admin === true

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-6 py-8 max-md:pt-16">
        {bot && (
          <Button
            variant="ghost"
            className="mb-3 h-auto p-0"
            onClick={() => onBotChange(undefined)}
          >
            ← Bots
          </Button>
        )}
        <h1 className="text-title font-medium text-ink">
          {bot ? `${botsByKey.get(bot)?.name ?? "Bot"} threads` : "Bots"}
        </h1>
        <p className="mt-1 text-meta text-ink-subtle">
          Threads started by Slack bots on the allowlist. They are read-only
          here: reply in the Slack thread to steer one.
          {!isAdmin && " Workspace admins manage which bots are allowed."}
        </p>

        {!bot &&
          (isAdmin ? (
            <div className="mt-4 rounded-control border border-line bg-panel">
              <AllowedSlackBotsSection onBotChange={onBotChange} />
            </div>
          ) : (
            <div className="mt-4 divide-y rounded-control border border-line bg-panel">
              {directory.isPending ? (
                <p className="p-4 text-meta text-ink-subtle">
                  Loading enabled bots…
                </p>
              ) : directory.isError ? (
                <div className="p-4">
                  <p className="text-label text-risk">
                    Enabled bots could not be loaded.
                  </p>
                  <Button
                    variant="outline"
                    size="compact"
                    className="mt-3"
                    onClick={() => void directory.refetch()}
                  >
                    Retry
                  </Button>
                </div>
              ) : bots.length === 0 ? (
                <p className="p-4 text-meta text-ink-subtle">
                  No Slack bots are enabled.
                </p>
              ) : (
                bots.map((entry) => (
                  <button
                    key={entry.key}
                    type="button"
                    onClick={() => onBotChange(entry.key)}
                    className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-hover focus-visible:outline-2 focus-visible:outline-primary"
                  >
                    <BotAvatar entry={entry} />
                    <span className="min-w-0 flex-1 truncate text-body font-medium">
                      {entry.name}
                    </span>
                    <span className="text-label text-positive">Enabled</span>
                    <span aria-hidden="true">→</span>
                  </button>
                ))
              )}
            </div>
          ))}

        {bot && (
          <div className="mt-6">
            {threadsQuery.isLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-16 w-full rounded-control" />
                <Skeleton className="h-16 w-full rounded-control" />
              </div>
            ) : threadsQuery.isError ? (
              <div className="flex flex-col items-center rounded-control border border-risk/30 bg-risk-bg px-6 py-12 text-center">
                <p className="text-label text-risk">
                  Bot threads could not be loaded.
                </p>
                <Button
                  type="button"
                  size="compact"
                  variant="outline"
                  className="mt-3"
                  onClick={() => void threadsQuery.refetch()}
                  disabled={threadsQuery.isFetching}
                >
                  {threadsQuery.isFetching ? "Retrying…" : "Retry"}
                </Button>
              </div>
            ) : threads.length === 0 ? (
              <div className="rounded-control border border-dashed border-line px-6 py-12 text-center text-meta text-ink-subtle">
                No bot threads yet. They appear here once an allowed bot
                mentions Open SWE in Slack.
              </div>
            ) : (
              <div className="space-y-2">
                {threads.map((thread) => (
                  <BotThreadRow
                    key={thread.id}
                    thread={thread}
                    entry={
                      thread.triggeringBot
                        ? botsByKey.get(thread.triggeringBot.key)
                        : undefined
                    }
                  />
                ))}
                {threadsQuery.data?.hasMore && (
                  <p className="pt-4 text-center text-meta text-ink-subtle">
                    Showing the {THREAD_LIMIT} most recent threads.
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function BotAvatar({ entry }: { entry: AllowedSlackBotEntry }) {
  return (
    <Avatar className="size-4">
      {entry.image_url && <AvatarImage src={entry.image_url} alt="" />}
      <AvatarFallback>
        <RobotIcon className="size-3" />
      </AvatarFallback>
    </Avatar>
  )
}

function BotThreadRow({
  thread,
  entry,
}: {
  thread: AgentThread
  entry?: AllowedSlackBotEntry
}) {
  const slackUrl = thread.sourceAppUrl ?? thread.sourceUrl
  return (
    <div className="flex items-center gap-3 rounded-control border border-line bg-panel px-4 py-3 transition-colors hover:border-ink-subtle/70">
      <Link
        to="/agents/$threadId"
        params={{ threadId: thread.id }}
        className="flex min-w-0 flex-1 items-center gap-3"
      >
        {thread.status === "running" ? (
          <CircleNotchIcon className="size-4 shrink-0 animate-spin text-primary" />
        ) : (
          <span
            className={cn(
              "size-2.5 shrink-0 rounded-full",
              thread.status === "error" || thread.status === "interrupted"
                ? "bg-risk"
                : thread.status === "finished"
                  ? "bg-positive"
                  : "bg-line"
            )}
          />
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-body font-medium text-ink">
            {thread.title}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-meta text-ink-subtle/70">
            <span>{STATUS_LABELS[thread.status]}</span>
            {thread.triggeringBot && (
              <span className="flex items-center gap-1">
                {entry && <BotAvatar entry={entry} />}
                {entry?.name ?? thread.triggeringBot.name}
              </span>
            )}
            {thread.repoFullName && <span>{thread.repoFullName}</span>}
            <span>{formatRelativeTime(thread.updatedAt)}</span>
          </div>
        </div>
      </Link>
      {slackUrl ? (
        <a
          href={slackUrl}
          target="_blank"
          rel="noreferrer"
          className="flex shrink-0 items-center gap-1 rounded-badge px-2 py-1 text-meta text-ink-subtle hover:bg-hover hover:text-ink"
          aria-label="Open the Slack thread"
        >
          <IoLogoSlack className="size-3.5" />
          Slack
        </a>
      ) : (
        <ArrowSquareOutIcon className="size-4 shrink-0 text-ink-subtle/70" />
      )}
    </div>
  )
}
